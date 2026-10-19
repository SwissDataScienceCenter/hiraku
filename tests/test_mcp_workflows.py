from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client, StdioServerParameters
from mcp.types import ElicitResult

from hiraku.config import HirakuConfig
from hiraku.mcp import create_server
from hiraku.record import Record

BUILTINS = {
    "workflow_list",
    "workflow_send_event",
    "session_list",
    "session_show",
    "session_cancel",
}


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
arguments:
  task: {required: true, description: Task record}
  iterations: {type: integer, default: 5, description: Experiments to run}
---
# Loop

Run {{iterations}} experiments on {{task}}.
"""


ECHO_PY = '''
from pydantic import BaseModel

from hiraku.errors import EventRejected
from hiraku.workflow import Instruction, Workflow, event, state


class Task(BaseModel):
    task: str
    summary: str | None = None


class Echo(Workflow):
    def start(self, task: str) -> Task:
        """Start working on a task."""
        return Task(task=task)

    @state("working", start=True)
    def working(self, task: Task) -> Instruction:
        return Instruction(f"Work on {task.task}.")

    @state("done", final=True)
    def done(self, task: Task) -> Instruction:
        return Instruction("Report the summary.", context={"summary": task.summary})

    @event(source="working", dest="done")
    def complete(self, task: Task, summary: str) -> None:
        """Report what was done."""
        if not summary.strip():
            raise EventRejected("Summarize what was done")
        task.summary = summary


WORKFLOW = Echo
'''


def write_loop(config: HirakuConfig) -> None:
    directory = config.workflows_dir / "loop"
    directory.mkdir()
    (directory / "workflow.md").write_text(LOOP, encoding="utf-8")


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
    # A final instruction is the workflow's text and context, with no events.
    assert text == (
        "# Loop\n\nRun 3 experiments on sep10.\n\nContext:\n```json\n"
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
    assert block["tool"] == "workflow_send_event"
    assert block["session_id"] == session_id
    assert block["instruction_id"] == 1
    assert [item["event"] for item in block["accepted"]] == ["complete"]
    # A prompt is a user message; without this line agents answered in chat.
    assert text.endswith("Answering in the chat does not reach the workflow.")
    assert "Send one of the accepted events with the workflow_send_event tool" in text
    assert shown["response"]["instruction"]["events"]["tool"] == "workflow_send_event"
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
    assert "accepts: complete" in result["wrong"]["message"]
    assert result["invalid"]["status"] == "rejected"
    assert "summary" in result["invalid"]["message"]
    assert result["done"]["completed"] is True
    assert result["invalid"]["instruction"]["events"]["tool"] == "workflow_send_event"
    assert result["done"]["instruction"] == {
        "text": "Report the summary.",
        "context": {"summary": "Tried three ideas"},
    }
    assert result["ambiguous"]["status"] == "rejected"
    assert "pass session_id" in result["ambiguous"]["message"]


def write_gated(config: HirakuConfig) -> None:
    source = ECHO_PY.replace(
        '@event(source="working", dest="done")',
        '@event(source="working", dest="done", requires_approval=True)',
    )
    directory = config.workflows_dir / "gated"
    directory.mkdir()
    (directory / "workflow.py").write_text(source, encoding="utf-8")
    (directory / "workflow.md").write_text(
        "---\nid: gated\n---\n# Gated\n", encoding="utf-8"
    )


def answer(action: str, **content: Any) -> Any:
    """Return an elicitation callback that answers every request the same way."""
    asked: list[Any] = []

    async def callback(context: Any, params: Any) -> Any:
        asked.append(params)
        return ElicitResult(action=action, content=content or None)

    callback.asked = asked  # type: ignore[attr-defined]
    return callback


# Before 2026-07-28 the server sends a nested elicitation request; from then
# on it returns InputRequiredResult, and the client calls the tool again.
PROTOCOLS = pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])


def send_gated(server: Any, elicitation: Any, mode: str) -> tuple[dict, dict, dict]:
    async def main() -> Any:
        async with Client(
            server, mode=mode, elicitation_callback=elicitation
        ) as client:
            first = payload(await client.call_tool("gated", {"task": "a"}))
            arguments = {"summary": "Tried three ideas"}
            sent = payload(
                await client.call_tool(
                    "workflow_send_event",
                    {"event": "complete", "instruction_id": 1, "arguments": arguments},
                )
            )
            shown = payload(
                await client.call_tool(
                    "session_show", {"session_id": first["session_id"]}
                )
            )
            return first, sent, shown

    return anyio.run(main)


@PROTOCOLS
def test_events_requiring_approval_ask_the_user(
    config: HirakuConfig, mode: str
) -> None:
    write_gated(config)
    approve = answer("accept", decision="approve")
    first, sent, shown = send_gated(create_server(config), approve, mode)
    accepted = first["instruction"]["events"]["accepted"]
    assert accepted[0]["requires_approval"] is True
    [params] = approve.asked
    assert "'complete'" in params.message
    assert "Tried three ideas" in params.message
    assert set(params.requested_schema["properties"]) == {"decision", "feedback"}
    # Codex cancels forms whose schema has top-level keys beyond these.
    assert set(params.requested_schema) == {"type", "properties", "required"}
    assert sent["status"] == "accepted"
    assert sent["completed"] is True
    assert shown["status"] == "completed"


@PROTOCOLS
@pytest.mark.parametrize(
    ("elicitation", "message"),
    [
        (
            answer("accept", decision="revise", feedback="Name the ideas"),
            "The user asked for changes: Name the ideas",
        ),
        (answer("decline"), "The user declined event 'complete'."),
        (answer("cancel"), "The user dismissed the approval"),
    ],
)
def test_unapproved_events_are_rejected_and_save_nothing(
    config: HirakuConfig, elicitation: Any, message: str, mode: str
) -> None:
    write_gated(config)
    elicitation.asked.clear()
    _, sent, shown = send_gated(create_server(config), elicitation, mode)
    assert sent["status"] == "rejected"
    assert sent["message"].startswith(message)
    assert "approval" not in sent
    assert sent["instruction_id"] == 1
    assert shown["status"] == "active"
    assert shown["response"]["instruction_id"] == 1


@PROTOCOLS
def test_clients_without_elicitation_get_approval_required(
    config: HirakuConfig, mode: str
) -> None:
    write_gated(config)
    _, sent, shown = send_gated(create_server(config), None, mode)
    assert sent["status"] == "approval_required"
    assert sent["approval"]["event"] == "complete"
    assert "--approve" in sent["message"]
    assert sent["instruction"]["events"]["tool"] == "workflow_send_event"
    assert shown["status"] == "active"


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
    assert result["shown"]["response"]["instruction_id"] == 1
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
        "from hiraku.workflow import Instruction, Workflow, state\n"
        "class Noisy(Workflow):\n"
        "    def start(self) -> None:\n"
        "        print('stray output')\n"
        "    @state('done', start=True, final=True)\n"
        "    def done(self) -> Instruction:\n"
        "        return Instruction('Done.', context={'ok': True})\n"
        "WORKFLOW = Noisy\n",
        encoding="utf-8",
    )
    (noisy / "workflow.md").write_text("---\nid: noisy\n---\n# Noisy\n")

    async def main() -> Any:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "hiraku.cli", "mcp"],
            cwd=config.root,
        )
        async with Client(parameters) as client:
            tools = await client.list_tools()
            result = await client.call_tool("noisy", {})
            return {tool.name for tool in tools.tools}, result

    names, result = anyio.run(main)
    assert "noisy" in names
    assert json.loads(result.content[0].text)["instruction"]["context"] == {"ok": True}


def test_workflows_read_current_project_state(config: HirakuConfig) -> None:
    server = create_server(config)
    Record.create("other-task", type="task", config=HirakuConfig.discover(config.root))

    async def scenario(client: Client, notifications: list[str]) -> dict[str, Any]:
        return payload(await client.call_tool("collaborative", {"entrypoint": "train"}))

    started = run(server, scenario)
    assert started["status"] == "rejected"
    assert "train" in started["message"]
    assert "other-task" in started["message"]
