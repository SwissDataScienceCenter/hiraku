from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client, StdioServerParameters

from hiraku.config import HirakuConfig
from hiraku.context import Context
from hiraku.errors import WorkflowError
from hiraku.mcp import create_server
from hiraku.record import Record
from hiraku.workflows.session import ClientInfo, Session, load_session
from hiraku.workflows.workflow import load_workflow

BUILTINS = {
    "workflow_list",
    "workflow_send_event",
    "session_list",
    "session_show",
    "session_cancel",
}


def test_codex_normalized_start_name_attributes_initial_entry(tmp_path):
    from hiraku.cli import _inject_client

    config = HirakuConfig.init(tmp_path, example="workflows", install_skill=False)
    injected = _inject_client(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "mcp__hiraku__count_steps",
            "cwd": str(config.root),
            "session_id": "codex-chat",
            "turn_id": "first-turn",
            "tool_input": {"count": 3},
        },
        "codex",
    )["hookSpecificOutput"]["updatedInput"]

    async def scenario(client, notifications):
        return payload(await client.call_tool("count-steps", injected))

    response = run(create_server(config), scenario)
    session = load_session(response["session_id"], config=config)
    assert len(session.history) == 1
    entry = session.history[0]
    assert entry.event == "start"
    assert entry.result.status == "accepted"
    assert entry.client_info == ClientInfo(
        client="codex", client_session_id="codex-chat", client_turn_id="first-turn"
    )
    assert session.state.count == 3


@pytest.mark.parametrize("client_name", ["codex", "claude"])
def test_hook_metadata_reaches_session_history(config, client_name):
    from hiraku.cli import _inject_client

    write_echo(config)
    server = create_server(config)

    async def scenario(client, notifications):
        def injected(tool, arguments, turn="turn-1"):
            return _inject_client(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": f"mcp__hiraku__{tool}",
                    "cwd": str(config.root),
                    "session_id": "chat-1",
                    "turn_id": turn,
                    "tool_input": arguments,
                },
                client_name,
            )["hookSpecificOutput"]["updatedInput"]

        first = payload(await client.call_tool("echo", injected("echo", {"task": "a"})))
        sid = first["session_id"]
        stored = load_session(sid, config=config)
        assert stored.history[0].client_info == ClientInfo(
            client=client_name,
            client_session_id="chat-1",
            client_turn_id="turn-1" if client_name == "codex" else None,
        )
        await client.call_tool("session_show", {"session_id": sid})
        assert load_session(sid, config=config) == stored
        arguments = {
            "session_id": sid,
            "instruction_id": first["instruction_id"],
            "event": "complete",
            "arguments": {"summary": "Done"},
        }
        accepted = payload(
            await client.call_tool(
                "workflow_send_event",
                injected("workflow_send_event", arguments, "turn-2"),
            )
        )
        retry = payload(
            await client.call_tool(
                "workflow_send_event",
                injected("workflow_send_event", arguments, "turn-3"),
            )
        )
        assert accepted["status"] == "accepted"
        assert retry["status"] == "stale"
        assert retry["instruction_id"] == accepted["instruction_id"] == 2
        assert retry["instruction"] == accepted["instruction"]
        stored = load_session(sid, config=config)
        await client.read_resource(f"hiraku://sessions/{sid}")
        assert load_session(sid, config=config) == stored
        return stored

    stored = run(server, scenario)
    assert [entry.event for entry in stored.history] == [
        "start",
        "complete",
        "complete",
    ]
    assert [entry.result.status for entry in stored.history] == [
        "accepted",
        "accepted",
        "stale",
    ]
    assert stored.history[1].client_info == ClientInfo(
        client=client_name,
        client_session_id="chat-1",
        client_turn_id="turn-2" if client_name == "codex" else None,
    )
    assert stored.history[2].client_info == ClientInfo(
        client=client_name,
        client_session_id="chat-1",
        client_turn_id="turn-3" if client_name == "codex" else None,
    )
    assert stored.history[2].instruction_id == 1
    assert all("kind" not in entry.model_dump() for entry in stored.history)
    assert stored.history[1].arguments == {"summary": "Done"}
    assert stored.history[2].arguments == {}


def test_failed_events_are_logged_but_cancellation_is_not(config):
    write_echo(config)
    server = create_server(config)
    info = {"client": "claude", "client_session_id": "chat"}

    async def scenario(client, notifications):
        first = payload(await client.call_tool("echo", {"task": "a"}))
        sid = first["session_id"]
        event = {
            "session_id": sid,
            "instruction_id": 1,
            "event": "complete",
            "arguments": {},
            "hiraku_client": info,
        }
        rejected = payload(await client.call_tool("workflow_send_event", event))
        assert rejected["status"] == "rejected"
        await client.call_tool("session_cancel", {"session_id": sid})
        rejected = payload(await client.call_tool("workflow_send_event", event))
        assert rejected["status"] == "rejected"
        return load_session(sid, config=config)

    stored = run(server, scenario)
    assert stored.status == "cancelled"
    assert stored.instruction_id == 1
    assert [entry.result.status for entry in stored.history[1:]] == [
        "rejected",
        "rejected",
    ]
    assert all(entry.client_info == ClientInfo(**info) for entry in stored.history[1:])


def test_unavailable_workflow_raises_before_event_execution(config, monkeypatch):
    write_echo(config)
    workflow = load_workflow("echo", config=config)
    session = Session.start(workflow, {"task": "a"})
    before = session._path.read_bytes()
    (config.workflows_dir / "echo" / "workflow.py").write_text(
        "raise RuntimeError('broken workflow')"
    )

    def fail(*args, **kwargs):
        raise AssertionError("must reject before calling run_event")

    monkeypatch.setattr(Session, "run_event", fail)

    async def scenario(client, notifications):
        result = await client.call_tool(
            "workflow_send_event",
            {"session_id": session.id, "instruction_id": 1, "event": "complete"},
        )
        assert result.is_error
        assert "Workflow unavailable" in result.content[0].text

    run(create_server(config), scenario)
    assert session._path.read_bytes() == before


def test_session_read_failure_leaves_history_unchanged(config, monkeypatch):
    write_echo(config)
    server = create_server(config)

    def fail(self, workflow=None):
        raise RuntimeError("inspection failed")

    async def scenario(client, notifications):
        first = payload(await client.call_tool("echo", {"task": "a"}))
        sid = first["session_id"]
        before = load_session(sid, config=config)
        monkeypatch.setattr(Session, "show", fail)
        result = await client.call_tool(
            "session_show",
            {"session_id": sid},
        )
        assert result.is_error
        stored = load_session(sid, config=config)
        assert stored == before
        return stored

    stored = run(server, scenario)
    assert len(stored.history) == 1
    assert stored.history[0].event == "start"
    assert stored.history[0].result.status == "accepted"


@pytest.fixture
def config(tmp_path: Path) -> HirakuConfig:
    selected = HirakuConfig.init(tmp_path, install_skill=False)
    Record.create(
        "task",
        type="task",
        entrypoints={"train": {"command": ["python", "train.py"]}},
        config=selected,
    )
    return selected


def run(server: Any, scenario: Any) -> Any:
    async def main() -> Any:
        notifications: list[str] = []

        async def handler(message: Any) -> None:
            notifications.append(getattr(message, "method", type(message).__name__))

        async with Client(server, mode="legacy", message_handler=handler) as client:
            return await scenario(client, notifications)

    return anyio.run(main)


def payload(result: Any) -> Any:
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def items(result: Any) -> list[Any]:
    """Return a list result, which the SDK sends as one content block per item."""
    assert not result.is_error, result.content[0].text
    return result.structured_content["result"]


LOOP = """\
---
id: loop
description: Run a loop.
---
# Loop

Run {{iterations}} experiments on {{task}}.
"""


LOOP_PY = r"""from typing import Annotated
from hiraku.markdown import render
from hiraku.workflows.graph import State, event, node, start
from hiraku.workflows.workflow import Instruction, Workflow


class Inputs(State):
    node: str = "done"
    task: str
    iterations: int


class Loop(Workflow):
    @start(dest="done")
    def start(
        self,
        *,
        task: Annotated[str, "Task record"],
        iterations: Annotated[int, "Experiments to run"] = 5,
    ) -> Inputs:
        return Inputs(task=task, iterations=iterations)

    @node("done", final=True)
    def prompt(self, data: Inputs) -> Instruction:
        values = data.model_dump(mode="json", exclude={"node"})
        return Instruction(render(self.markdown, values), data=values)


WORKFLOW = Loop
"""


ECHO_PY = r'''from hiraku.errors import EventRejected
from hiraku.workflows.graph import State, event, node, start
from hiraku.workflows.workflow import Instruction, Workflow


class Task(State):
    node: str = "working"
    task: str
    summary: str | None = None


class Echo(Workflow):
    @start(dest="working")
    def start(self, *, task: str) -> Task:
        """Start working on a task."""
        return Task(task=task)

    @node("working")
    def working(self, task: Task) -> Instruction:
        return Instruction(f"Work on {task.task}.")

    @node("done", final=True)
    def done(self, task: Task) -> Instruction:
        return Instruction("Report the summary.", data={"summary": task.summary})

    @event(source="working", dest="done")
    def complete(self, task: Task, *, summary: str) -> Task:
        """Report what was done."""
        if not summary.strip():
            raise EventRejected("Summarize what was done")
        task = task.model_copy(update={"summary": summary})
        return task.model_copy(update={"node": "done"})


WORKFLOW = Echo
'''


def write_loop(config: HirakuConfig) -> None:
    directory = config.workflows_dir / "loop"
    directory.mkdir()
    (directory / "workflow.md").write_text(LOOP, encoding="utf-8")
    (directory / "workflow.py").write_text(LOOP_PY, encoding="utf-8")


def test_instruction_research_resources(config):
    context_path = config.contexts_dir / "protocol" / "context.md"
    Context(id="protocol", markdown="Original protocol").save(context_path)
    directory = config.workflows_dir / "echo"
    directory.mkdir()
    (directory / "workflow.md").write_text("---\nid: echo\n---\nEcho")
    source = ECHO_PY.replace(
        'return Instruction(f"Work on {task.task}.")',
        'return Instruction(f"Work on {task.task}.", '
        'context={"contexts": ["protocol"], "records": ["task"]})',
    ).replace(
        'data={"summary": task.summary}',
        'data={"summary": task.summary}, '
        'context={"contexts": ["protocol"], "records": ["task"]}',
    )
    (directory / "workflow.py").write_text(source)

    async def scenario(client, notifications):
        started = await client.call_tool("echo", {"task": "task"})
        response = payload(started)
        assert started.structured_content == response
        assert response["instruction"]["context"] == {
            "contexts": ["protocol"],
            "records": ["task"],
        }

        async def check_resources(result, protocol):
            assert [item.type for item in result.content] == [
                "text",
                "resource_link",
                "resource_link",
            ]
            for block, uri, mime in zip(
                result.content[1:],
                ["hiraku://contexts/protocol", "hiraku://records/task"],
                ["text/markdown", "application/json"],
                strict=True,
            ):
                assert str(block.uri) == uri
                assert block.mime_type == mime
                read = await client.read_resource(uri)
                if mime == "text/markdown":
                    assert read.contents[0].text == protocol
                else:
                    assert json.loads(read.contents[0].text)["id"] == "task"

        await check_resources(started, "Original protocol")
        Context(id="protocol", markdown="Updated protocol").save(
            context_path, overwrite=True
        )
        shown = await client.call_tool(
            "session_show", {"session_id": response["session_id"]}
        )
        assert payload(shown)["instruction"] == response["instruction"]
        await check_resources(shown, "Updated protocol")
        event = {
            "session_id": response["session_id"],
            "instruction_id": response["instruction_id"],
            "event": "complete",
            "arguments": {"summary": "Done"},
        }
        completed = await client.call_tool("workflow_send_event", event)
        assert payload(completed)["session_status"] == "completed"
        await check_resources(completed, "Updated protocol")
        stale = await client.call_tool(
            "workflow_send_event", {**event, "arguments": {"summary": "Other"}}
        )
        assert payload(stale)["status"] == "stale"
        await check_resources(stale, "Updated protocol")
        context_path.unlink()
        missing = await client.call_tool(
            "session_show", {"session_id": response["session_id"]}
        )
        assert payload(missing)["session_status"] == "completed"
        assert missing.content[1].type == "resource_link"
        assert "unavailable" in missing.content[2].text

    run(create_server(config), scenario)


def test_markdown_only_tools_prompts_and_description(config):
    directory = config.workflows_dir / "markdown"
    directory.mkdir()
    (directory / "workflow.md").write_text(
        "---\nid: markdown\ndescription: A Markdown research prompt.\n"
        "arguments:\n  num_iterations: 5\n  max_val: 0.5\n  gate: true\n"
        "---\nRun {{num_iterations}} iterations at {{max_val}}; gate={{gate}}."
    )

    async def scenario(client, notifications):
        tools = await client.list_tools()
        tool = next(tool for tool in tools.tools if tool.name == "markdown")
        started = payload(
            await client.call_tool(
                "markdown",
                {"num_iterations": 2, "gate": False},
            )
        )
        prompted = await client.get_prompt(
            "markdown",
            {"num_iterations": "3", "max_val": "0.25", "gate": "false"},
        )
        invalid = await client.get_prompt("markdown", {"num_iterations": "invalid"})
        return tool, started, prompted, invalid

    tool, started, prompted, invalid = run(create_server(config), scenario)
    assert "A Markdown research prompt." in tool.description
    assert tool.input_schema["properties"]["num_iterations"]["type"] == "integer"
    assert tool.input_schema["properties"]["max_val"]["type"] == "number"
    assert tool.input_schema["properties"]["gate"]["type"] == "boolean"
    assert started["session_status"] == "completed"
    assert started["instruction"]["text"] == "Run 2 iterations at 0.5; gate=false."
    assert "Run 3 iterations at 0.25; gate=false." in prompted.messages[0].content.text
    assert "did not start" in invalid.messages[0].content.text


def write_echo(config: HirakuConfig) -> None:
    directory = config.workflows_dir / "echo"
    directory.mkdir()
    (directory / "workflow.py").write_text(ECHO_PY, encoding="utf-8")
    (directory / "workflow.md").write_text(
        "---\nid: echo\n---\n# Echo\n", encoding="utf-8"
    )


def test_tools_and_prompts_are_fixed_per_workflow(config: HirakuConfig) -> None:
    write_loop(config)
    server = create_server(config)

    async def scenario(client: Client, notifications: list[str]) -> Any:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        prompts = {item.name: item for item in (await client.list_prompts()).prompts}
        return tools, prompts

    tools, prompts = run(server, scenario)
    workflows = {"collaborative", "loop"}
    assert BUILTINS | workflows <= set(tools)
    assert workflows <= set(prompts)
    loop = tools["loop"].input_schema
    assert loop["required"] == ["task"]
    assert loop["properties"]["iterations"]["default"] == 5
    assert loop["properties"]["task"]["description"] == "Task record"
    assert loop["properties"]["iterations"]["description"] == "Experiments to run"
    assert prompts["loop"].arguments[0].description == "Task record"
    arguments = {item.name: item.required for item in prompts["loop"].arguments}
    assert arguments == {"task": True, "iterations": False}
    assert tools["collaborative"].description.startswith("Start a collaborative")


def test_prompts_start_sessions(config: HirakuConfig) -> None:
    write_loop(config)
    server = create_server(config)

    async def scenario(client: Client, notifications: list[str]) -> Any:
        started = await client.get_prompt("loop", {"task": "sep10", "iterations": "3"})
        invalid = await client.get_prompt("loop", {"task": "x", "iterations": "many"})
        listing = items(await client.call_tool("session_list", {}))
        return started, invalid, listing

    started, invalid, listing = run(server, scenario)
    text = started.messages[0].content.text
    # A final instruction is the workflow's text and data, with no events.
    assert text == (
        "# Loop\n\nRun 3 experiments on sep10.\n\nData:\n```json\n"
        '{\n "task": "sep10",\n "iterations": 3\n}\n```'
    )
    assert listing[0]["status"] == "completed"
    assert len(listing) == 1
    assert "did not start" in invalid.messages[0].content.text
    assert "iterations" in invalid.messages[0].content.text


def test_prompts_render_the_events_block_and_tools_explain_it(
    config: HirakuConfig,
) -> None:
    write_echo(config)
    server = create_server(config)

    async def scenario(client: Client, notifications: list[str]) -> Any:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        started = await client.get_prompt("echo", {"task": "a"})
        listing = items(await client.call_tool("session_list", {}))
        shown = payload(
            await client.call_tool(
                "session_show", {"session_id": listing[0]["session_id"]}
            )
        )
        return tools, started.messages[0].content.text, listing, shown

    tools, text, listing, shown = run(server, scenario)
    session_id = listing[0]["session_id"]
    assert text.startswith("Work on a.\n\nEvents:\n```json\n")
    block = json.loads(text.split("```json\n", 1)[1].rsplit("\n```", 1)[0])
    assert set(block) == {"tool", "session_id", "instruction_id", "accepted"}
    assert block["tool"] == "workflow_send_event"
    assert block["session_id"] == session_id
    assert block["instruction_id"] == 1
    assert [item["event"] for item in block["accepted"]] == ["complete"]
    # A prompt is a user message; without this line agents answered in chat.
    assert text.endswith("Answering in the chat does not reach the workflow.")
    assert "Send one of the accepted events with the workflow_send_event tool" in text
    assert shown["instruction"]["events"]["tool"] == "workflow_send_event"
    description = tools["workflow_send_event"].description
    assert "events block" in description
    assert "An instruction without an" in description


def test_workflow_send_event_continues_any_session(config: HirakuConfig) -> None:
    write_echo(config)
    server = create_server(config)

    async def scenario(client: Client, notifications: list[str]) -> dict[str, Any]:
        none = payload(
            await client.call_tool(
                "workflow_send_event", {"event": "complete", "instruction_id": 1}
            )
        )
        first = payload(await client.call_tool("echo", {"task": "a"}))
        wrong = payload(
            await client.call_tool(
                "workflow_send_event", {"event": "submit_run", "instruction_id": 1}
            )
        )
        invalid = payload(
            await client.call_tool(
                "workflow_send_event",
                {"event": "complete", "instruction_id": 1, "arguments": {}},
            )
        )
        done = payload(
            await client.call_tool(
                "workflow_send_event",
                {
                    "event": "complete",
                    "instruction_id": 1,
                    "arguments": {"summary": "Tried three ideas"},
                },
            )
        )
        payload(await client.call_tool("echo", {"task": "b"}))
        payload(await client.call_tool("echo", {"task": "c"}))
        ambiguous = payload(
            await client.call_tool(
                "workflow_send_event",
                {"event": "complete", "instruction_id": 1, "arguments": {}},
            )
        )
        return {
            "none": none,
            "first": first,
            "wrong": wrong,
            "invalid": invalid,
            "done": done,
            "ambiguous": ambiguous,
        }

    result = run(server, scenario)
    events_block = result["first"]["instruction"]["events"]
    assert "No session is active" in result["none"]["message"]
    assert "guide" not in result["first"]
    assert events_block["tool"] == "workflow_send_event"
    assert events_block["session_id"] == result["first"]["session_id"]
    assert events_block["instruction_id"] == 1
    assert events_block["accepted"][0]["event"] == "complete"
    assert events_block["accepted"][0]["arguments"]["required"] == ["summary"]
    assert result["wrong"]["status"] == "rejected"
    assert "Unknown event" in result["wrong"]["message"]
    assert result["invalid"]["status"] == "rejected"
    assert "summary" in result["invalid"]["message"]
    assert result["done"]["session_status"] == "completed"
    assert result["invalid"]["instruction"]["events"]["instruction_id"] == 1
    assert result["done"]["instruction"] == {
        "text": "Report the summary.",
        "data": {"summary": "Tried three ideas"},
    }
    assert result["ambiguous"]["status"] == "rejected"
    assert "pass session_id" in result["ambiguous"]["message"]


def test_session_tools_and_resources(config: HirakuConfig) -> None:
    write_echo(config)
    server = create_server(config)

    async def scenario(client: Client, notifications: list[str]) -> dict[str, Any]:
        started = payload(await client.call_tool("echo", {"task": "task"}))
        session_id = started["session_id"]
        listing = items(await client.call_tool("session_list", {}))
        shown = payload(
            await client.call_tool("session_show", {"session_id": session_id})
        )
        resource = await client.read_resource(f"hiraku://sessions/{session_id}")
        workflow = await client.read_resource("hiraku://workflows/echo")
        graph = await client.read_resource("hiraku://workflows/collaborative/graph")
        echo_graph = await client.read_resource("hiraku://workflows/echo/graph")
        templates = await client.list_resource_templates()
        cancelled = payload(
            await client.call_tool("session_cancel", {"session_id": session_id})
        )
        missing = await client.call_tool("session_show", {"session_id": "nope"})
        return {
            "started": started,
            "listing": listing,
            "shown": shown,
            "resource": json.loads(resource.contents[0].text),
            "workflow": workflow.contents[0].text,
            "graph": graph.contents[0].text,
            "echo_graph": echo_graph.contents[0].text,
            "templates": {item.uri_template for item in templates.resource_templates},
            "cancelled": cancelled,
            "missing": missing,
        }

    result = run(server, scenario)
    session_id = result["started"]["session_id"]
    assert result["listing"][0]["session_id"] == session_id
    assert result["shown"]["instruction_id"] == 1
    assert "orientation" not in result["shown"]
    assert "guide" not in result["shown"]
    assert result["resource"]["session_id"] == session_id
    assert result["workflow"] == "# Echo"
    assert result["graph"].startswith("stateDiagram-v2\n  [*] --> draft\n")
    assert "  draft --> implement: drafted\n" in result["graph"]
    assert result["echo_graph"] == (
        "stateDiagram-v2\n"
        "  [*] --> working\n"
        "  working --> done: complete\n"
        "  done --> [*]\n"
    )
    assert result["templates"] >= {
        "hiraku://sessions/{session_id}",
        "hiraku://workflows/{workflow_id}",
    }
    assert result["cancelled"]["status"] == "cancelled"
    assert result["missing"].is_error
    assert "not found" in result["missing"].content[0].text


def test_stdio_server_keeps_protocol_output_clean(config: HirakuConfig) -> None:
    noisy = config.workflows_dir / "noisy"
    noisy.mkdir()
    (noisy / "workflow.py").write_text(
        "from hiraku.workflows.graph import State, node, start\n"
        "from hiraku.workflows.workflow import Instruction, Workflow\n"
        "class Noisy(Workflow):\n"
        '    @start(dest="done")\n'
        "    def start(self) -> State:\n"
        "        print('stray output')\n"
        '        return State(node="done")\n'
        "    @node('done', final=True)\n"
        "    def done(self, state: State) -> Instruction:\n"
        "        return Instruction('Done.', data={'ok': True})\n"
        "WORKFLOW = Noisy\n",
        encoding="utf-8",
    )
    (noisy / "workflow.md").write_text("---\nid: noisy\n---\n# Noisy\n")

    async def main() -> Any:
        configured = tomllib.loads((config.root / ".codex/config.toml").read_text())[
            "mcp_servers"
        ]["hiraku"]
        parameters = StdioServerParameters(
            command=configured["command"],
            args=configured["args"],
            cwd=config.root,
            env={"PATH": os.environ["PATH"]},
        )
        async with Client(parameters) as client:
            tools = await client.list_tools()
            result = await client.call_tool(
                "noisy",
                {
                    "hiraku_client": {
                        "client": "codex",
                        "client_session_id": "chat",
                    }
                },
            )
            return {tool.name for tool in tools.tools}, result

    names, result = anyio.run(main)
    assert "noisy" in names
    assert json.loads(result.content[0].text)["instruction"]["data"] == {"ok": True}
    stored = load_session(payload(result)["session_id"], config=config)
    assert stored.history[0].client_info == ClientInfo(
        client="codex", client_session_id="chat"
    )


def test_runs_workflow_id_is_reserved(config, caplog):
    directory = config.workflows_dir / "runs"
    directory.mkdir()
    (directory / "workflow.md").write_text("---\nid: runs\n---\nRun a task.")
    with pytest.raises(WorkflowError, match="reserved"):
        load_workflow("runs", config=config)
    server = create_server(config)

    async def scenario(client, notifications):
        tools = (await client.list_tools()).tools
        workflows = items(await client.call_tool("workflow_list", {}))
        return tools, workflows

    tools, workflows = run(server, scenario)
    assert "reserved" in caplog.text
    assert all(workflow["id"] != "runs" for workflow in workflows)
    assert (
        "record_id"
        in next(t for t in tools if t.name == "runs").input_schema["properties"]
    )


def test_workflows_read_current_project_state(config: HirakuConfig) -> None:
    server = create_server(config)
    Record.create("other-task", type="task", config=HirakuConfig.discover(config.root))

    async def scenario(client: Client, notifications: list[str]) -> Any:
        return await client.call_tool("collaborative", {"entrypoint": "train"})

    started = run(server, scenario)
    assert started.is_error
    assert "train" in started.content[0].text
    assert "other-task" in started.content[0].text
