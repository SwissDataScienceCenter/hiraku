from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client, StdioServerParameters

from hiraku.config import HirakuConfig
from hiraku.mcp import create_server
from hiraku.record import Record

BUILTINS = {
    "workflow_list",
    "workflow_reply",
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


def write_loop(config: HirakuConfig) -> None:
    directory = config.workflows_dir / "loop"
    directory.mkdir()
    (directory / "workflow.md").write_text(LOOP, encoding="utf-8")


def test_tools_and_prompts_are_fixed_per_workflow(config: HirakuConfig) -> None:
    write_loop(config)
    server = create_server(config)

    async def scenario(client: Client, notifications: list[str]) -> Any:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        prompts = {item.name: item for item in (await client.list_prompts()).prompts}
        return tools, prompts

    tools, prompts = run(server, scenario)
    workflows = {"autodataset", "collaborative", "loop"}
    assert BUILTINS | workflows <= set(tools)
    assert workflows <= set(prompts)
    loop = tools["loop"].input_schema
    assert loop["required"] == ["task"]
    assert loop["properties"]["iterations"]["default"] == 5
    arguments = {item.name: item.required for item in prompts["loop"].arguments}
    assert arguments == {"task": True, "iterations": False}
    assert tools["autodataset"].description.startswith("Start a autodataset")


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
    assert text.startswith("# Loop\n\nRun 3 experiments on sep10.")
    assert '"reply": "complete"' in text
    assert f"session `{listing[0]['session_id']}`" in text
    assert len(listing) == 1
    assert "did not start" in invalid.messages[0].content.text
    assert "iterations" in invalid.messages[0].content.text


def test_workflow_reply_continues_any_session(config: HirakuConfig) -> None:
    write_loop(config)
    server = create_server(config)

    async def scenario(client: Client, notifications: list[str]) -> dict[str, Any]:
        none = payload(
            await client.call_tool(
                "workflow_reply", {"reply": "complete", "instruction_id": 1}
            )
        )
        first = payload(await client.call_tool("loop", {"task": "a"}))
        wrong = payload(
            await client.call_tool(
                "workflow_reply", {"reply": "submit_run", "instruction_id": 1}
            )
        )
        invalid = payload(
            await client.call_tool(
                "workflow_reply",
                {"reply": "complete", "instruction_id": 1, "arguments": {}},
            )
        )
        done = payload(
            await client.call_tool(
                "workflow_reply",
                {
                    "reply": "complete",
                    "instruction_id": 1,
                    "arguments": {"summary": "Tried three ideas"},
                },
            )
        )
        payload(await client.call_tool("loop", {"task": "b"}))
        payload(await client.call_tool("loop", {"task": "c"}))
        ambiguous = payload(
            await client.call_tool(
                "workflow_reply",
                {"reply": "complete", "instruction_id": 1, "arguments": {}},
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
    replies = result["first"]["instruction"]["replies"]
    assert "No session is active" in result["none"]["message"]
    assert "workflow_reply" in result["first"]["guide"]
    assert replies[0]["reply"] == "complete"
    assert replies[0]["arguments"]["required"] == ["summary"]
    assert result["wrong"]["status"] == "rejected"
    assert "accepts: complete" in result["wrong"]["message"]
    assert result["invalid"]["status"] == "rejected"
    assert "summary" in result["invalid"]["message"]
    assert result["done"]["result"] == {"summary": "Tried three ideas"}
    assert result["ambiguous"]["status"] == "rejected"
    assert "pass session_id" in result["ambiguous"]["message"]


def test_session_tools_and_resources(config: HirakuConfig) -> None:
    server = create_server(config)

    async def scenario(client: Client, notifications: list[str]) -> dict[str, Any]:
        started = payload(
            await client.call_tool("autodataset", {"task": "task", "count": 1})
        )
        session_id = started["session_id"]
        listing = items(await client.call_tool("session_list", {}))
        shown = payload(
            await client.call_tool("session_show", {"session_id": session_id})
        )
        resource = await client.read_resource(f"hiraku://sessions/{session_id}")
        workflow = await client.read_resource("hiraku://workflows/autodataset")
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
            "templates": {item.uri_template for item in templates.resource_templates},
            "cancelled": cancelled,
            "missing": missing,
        }

    result = run(server, scenario)
    session_id = result["started"]["session_id"]
    assert result["listing"][0]["session_id"] == session_id
    assert result["shown"]["response"]["instruction_id"] == 1
    assert result["shown"]["orientation"].startswith("# Autodataset")
    assert "session_show" in result["shown"]["guide"]
    assert result["resource"]["session_id"] == session_id
    assert result["workflow"].startswith("# Autodataset")
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
        "from hiraku.workflow import Session, Workflow\n"
        "class S(Session):\n    pass\n"
        "class Noisy(Workflow):\n"
        "    def start(self):\n"
        "        print('stray output')\n"
        "        return self.completion(S(), {'ok': True})\n"
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
    assert json.loads(result.content[0].text)["result"] == {"ok": True}


def test_workflows_read_current_project_state(config: HirakuConfig) -> None:
    server = create_server(config)
    Record.create("other-task", type="task", config=HirakuConfig.discover(config.root))

    async def scenario(client: Client, notifications: list[str]) -> dict[str, Any]:
        return payload(await client.call_tool("collaborative", {"entrypoint": "train"}))

    started = run(server, scenario)
    assert started["status"] == "rejected"
    assert "train" in started["message"]
    assert "other-task" in started["message"]
