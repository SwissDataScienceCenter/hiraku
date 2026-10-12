from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client, StdioServerParameters

from hiraku.config import HirakuConfig
from hiraku.mcp import HirakuMCPServer
from hiraku.record import Record

BUILTINS = {
    "workflow_list",
    "workflow_activate",
    "workflow_deactivate",
    "session_list",
    "session_show",
    "session_cancel",
    "project_status",
    "record_list",
    "record_show",
    "record_create",
    "record_update",
    "runs",
}


@pytest.fixture
def config(tmp_path: Path) -> HirakuConfig:
    selected = HirakuConfig.init(tmp_path, install_skill=False, install_mcp=False)
    Record.create(
        "task",
        type="task",
        markdown="# Task\n" + "x" * 9000,
        metrics=[{"name": "acc", "direction": "maximize"}],
        entrypoints={"train": {"command": ["python", "train.py"]}},
        config=selected,
    )
    (tmp_path / "runs.csv").write_text(
        "id,record_id,entrypoint,metric.acc\n"
        "a,task,train,0.5\nb,task,train,0.9\nc,other,train,0.7\n",
        encoding="utf-8",
    )
    return selected


def run(server: HirakuMCPServer, scenario: Any) -> Any:
    async def main() -> Any:
        notifications: list[str] = []

        async def handler(message: Any) -> None:
            notifications.append(getattr(message, "method", type(message).__name__))

        async with Client(
            server.server, mode="legacy", message_handler=handler
        ) as client:
            return await scenario(client, notifications)

    return anyio.run(main)


def payload(result: Any) -> Any:
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def test_tools_reflect_the_configured_workflows(config: HirakuConfig) -> None:
    config.set_mcp_workflows(["autodataset"])
    server = HirakuMCPServer(HirakuConfig.discover(config.root))

    async def scenario(client: Client, notifications: list[str]) -> set[str]:
        return {tool.name for tool in (await client.list_tools()).tools}

    names = run(server, scenario)
    assert names >= BUILTINS
    assert "autodataset_start" in names
    assert not any(name.startswith("autoresearch_") for name in names)


def test_command_line_override_exposes_one_workflow(config: HirakuConfig) -> None:
    server = HirakuMCPServer(config, exposed=["autoresearch"])

    async def scenario(client: Client, notifications: list[str]) -> set[str]:
        return {tool.name for tool in (await client.list_tools()).tools}

    names = run(server, scenario)
    assert "autoresearch_start" in names
    assert "autodataset_start" not in names


def test_activation_notifies_and_changes_tools(config: HirakuConfig) -> None:
    config.set_mcp_workflows(["autoresearch"])
    server = HirakuMCPServer(HirakuConfig.discover(config.root))

    async def scenario(client: Client, notifications: list[str]) -> dict[str, Any]:
        inactive = await client.call_tool("autodataset_start", {"task": "task"})
        activated = payload(
            await client.call_tool("workflow_activate", {"workflow_id": "autodataset"})
        )
        tools = {tool.name for tool in (await client.list_tools()).tools}
        started = payload(await client.call_tool("autodataset_start", {"task": "task"}))
        await anyio.sleep(0.05)
        return {
            "inactive": inactive,
            "activated": activated,
            "tools": tools,
            "started": started,
            "notifications": list(notifications),
        }

    result = run(server, scenario)
    assert result["inactive"].is_error
    assert "call workflow_activate first" in result["inactive"].content[0].text
    assert result["activated"]["changed"] is True
    assert "autodataset_start" in result["tools"]
    assert result["started"]["status"] == "accepted"
    assert "notifications/tools/list_changed" in result["notifications"]
    assert "notifications/prompts/list_changed" in result["notifications"]
    persisted = HirakuConfig.discover(config.root).mcp
    assert persisted is not None
    assert persisted.workflows == ["autoresearch", "autodataset"]


def test_session_tools_and_resources(config: HirakuConfig) -> None:
    server = HirakuMCPServer(config)

    async def scenario(client: Client, notifications: list[str]) -> dict[str, Any]:
        started = payload(
            await client.call_tool("autodataset_start", {"task": "task", "count": 1})
        )
        session_id = started["session_id"]
        listing = payload(await client.call_tool("session_list", {}))
        shown = payload(
            await client.call_tool("session_show", {"session_id": session_id})
        )
        resource = await client.read_resource(f"hiraku://sessions/{session_id}")
        workflow = await client.read_resource("hiraku://workflows/autodataset")
        record = await client.read_resource("hiraku://records/task")
        resources = await client.list_resources()
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
            "record": record.contents[0].text,
            "resources": {item.uri for item in resources.resources},
            "templates": {item.uri_template for item in templates.resource_templates},
            "cancelled": cancelled,
            "missing": missing,
        }

    result = run(server, scenario)
    session_id = result["started"]["session_id"]
    assert result["listing"][0]["session_id"] == session_id
    assert result["shown"]["last_response"]["instruction_id"] == 1
    assert result["resource"]["session_id"] == session_id
    assert result["workflow"].startswith("# Autodataset")
    assert result["record"].startswith("---\n")
    assert "hiraku://workflows/autoresearch" in result["resources"]
    assert result["templates"] == {
        "hiraku://records/{record_id}",
        "hiraku://sessions/{session_id}",
    }
    assert result["cancelled"]["status"] == "cancelled"
    assert result["missing"].is_error


def test_prompts_start_active_workflows(config: HirakuConfig) -> None:
    server = HirakuMCPServer(config, exposed=["autoresearch"])

    async def scenario(client: Client, notifications: list[str]) -> Any:
        prompts = await client.list_prompts()
        prompt = await client.get_prompt("autoresearch", {"iterations": "2"})
        return prompts, prompt

    prompts, prompt = run(server, scenario)
    assert [item.name for item in prompts.prompts] == [
        "autoresearch",
        "autoresearch_resume",
    ]
    arguments = {item.name: item for item in prompts.prompts[0].arguments}
    assert arguments["iterations"].required is False
    assert "default 10" in arguments["iterations"].description
    text = prompt.messages[0].content.text
    assert "autoresearch_start" in text
    assert '"iterations": "2"' in text


def test_record_and_run_tools(config: HirakuConfig) -> None:
    server = HirakuMCPServer(config)

    async def scenario(client: Client, notifications: list[str]) -> dict[str, Any]:
        created = payload(
            await client.call_tool(
                "record_create",
                {
                    "record_id": "exp",
                    "parent_id": "task",
                    "type": "experiment",
                    "fields": {"hypothesis": "More data helps"},
                },
            )
        )
        reserved = await client.call_tool(
            "record_create", {"record_id": "bad", "fields": {"parent_id": "task"}}
        )
        updated = payload(
            await client.call_tool(
                "record_update",
                {
                    "record_id": "exp",
                    "fields": {"conclusion": "Done"},
                    "unset": ["hypothesis"],
                },
            )
        )
        shown = payload(await client.call_tool("record_show", {"record_id": "exp"}))
        task = payload(await client.call_tool("record_show", {"record_id": "task"}))
        listing = payload(await client.call_tool("record_list", {}))
        runs = payload(
            await client.call_tool("runs", {"record_id": "task", "sort": "acc"})
        )
        all_runs = payload(
            await client.call_tool("runs", {"record_id": "task", "all": True})
        )
        status = payload(await client.call_tool("project_status", {}))
        invalid = await client.call_tool("runs", {"limit": 0})
        return {
            "created": created,
            "reserved": reserved,
            "updated": updated,
            "shown": shown,
            "task": task,
            "listing": listing,
            "runs": runs,
            "all_runs": all_runs,
            "status": status,
            "invalid": invalid,
        }

    result = run(server, scenario)
    assert result["created"]["created"] == "exp"
    assert result["reserved"].is_error
    assert result["updated"] == {"updated": "exp"}
    values = result["shown"]["values"]
    assert values["conclusion"] == "Done"
    assert "hypothesis" not in values
    assert values["entrypoints"]["train"]["command"] == ["python", "train.py"]
    assert result["shown"]["sources"]["entrypoints"] == {"train": "task"}
    assert result["task"]["markdown_truncated"] is True
    assert result["task"]["markdown_resource"] == "hiraku://records/task"
    assert len(result["task"]["markdown"]) == 8000
    assert [item["id"] for item in result["listing"]] == ["exp", "task"]
    assert [item["id"] for item in result["runs"]] == ["b", "a"]
    assert {item["id"] for item in result["all_runs"]} == {"a", "b", "c"}
    assert result["status"]["active_record_id"] == "task"
    assert result["invalid"].is_error
    assert HirakuConfig.discover(config.root).active_record_id == "task"


def test_stdio_server_keeps_protocol_output_clean(config: HirakuConfig) -> None:
    noisy = config.workflows_dir / "noisy"
    noisy.mkdir()
    (noisy / "workflow.py").write_text(
        "from hiraku.workflow import Session, Workflow, tool\n"
        "class S(Session):\n    pass\n"
        "class Noisy(Workflow):\n"
        "    @tool\n"
        "    def start(self):\n"
        "        print('stray output')\n"
        "        return self.completion(S(), {'ok': True})\n"
        "WORKFLOW = Noisy\n",
        encoding="utf-8",
    )
    (noisy / "workflow.md").write_text("---\nid: noisy\n---\n# Noisy\n")
    config.set_mcp_workflows(["noisy"])

    async def main() -> Any:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "hiraku.cli", "mcp", "--project", str(config.root)],
        )
        async with Client(parameters) as client:
            tools = await client.list_tools()
            result = await client.call_tool("noisy_start", {})
            return {tool.name for tool in tools.tools}, result

    names, result = anyio.run(main)
    assert "noisy_start" in names
    assert json.loads(result.content[0].text)["result"] == {"ok": True}


def test_workflow_resources_and_prompts_follow_activation(config: HirakuConfig) -> None:
    config.set_mcp_workflows(["autodataset"])
    server = HirakuMCPServer(HirakuConfig.discover(config.root))

    async def scenario(client: Client, notifications: list[str]) -> dict[str, Any]:
        before = {item.uri for item in (await client.list_resources()).resources}
        await client.call_tool("workflow_activate", {"workflow_id": "autoresearch"})
        after = {item.uri for item in (await client.list_resources()).resources}
        policy = await client.read_resource("hiraku://workflows/autoresearch/policy")
        prompts = {item.name for item in (await client.list_prompts()).prompts}
        resume = await client.get_prompt("autoresearch_resume", {})
        await anyio.sleep(0.05)
        return {
            "before": before,
            "after": after,
            "policy": policy.contents[0].text,
            "prompts": prompts,
            "resume": resume.messages[0].content.text,
            "notifications": list(notifications),
        }

    result = run(server, scenario)
    assert "hiraku://workflows/autoresearch/policy" not in result["before"]
    assert "hiraku://workflows/autoresearch/policy" in result["after"]
    assert "must be `finished`" in result["policy"]
    assert "autoresearch_resume" in result["prompts"]
    assert "session_list" in result["resume"]
    assert "notifications/resources/list_changed" in result["notifications"]


def test_server_reads_current_project_state(config: HirakuConfig) -> None:
    server = HirakuMCPServer(config)
    Record.create("other-task", type="task", config=HirakuConfig.discover(config.root))
    assert HirakuConfig.discover(config.root).active_record_id == "other-task"

    async def scenario(client: Client, notifications: list[str]) -> dict[str, Any]:
        status = payload(await client.call_tool("project_status", {}))
        shown = payload(await client.call_tool("record_show", {}))
        await client.call_tool("workflow_deactivate", {"workflow_id": "autodataset"})
        started = payload(
            await client.call_tool("autoresearch_start", {"entrypoint": "train"})
        )
        return {"status": status, "shown": shown, "started": started}

    result = run(server, scenario)
    assert result["status"]["active_record_id"] == "other-task"
    assert result["shown"]["id"] == "other-task"
    saved = HirakuConfig.discover(config.root)
    assert saved.active_record_id == "other-task"
    assert "autodataset" not in saved.mcp.workflows
    assert result["started"]["status"] == "rejected"
    assert "several entrypoints" not in result["started"]["message"]
    assert "train" in result["started"]["message"]
