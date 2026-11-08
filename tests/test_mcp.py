from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from click.testing import CliRunner

pytest.importorskip("mcp")

from mcp import (  # noqa: E402  # noqa: E402
    Client,
    ClientSession,
    StdioServerParameters,
    types,
)
from mcp.client.stdio import stdio_client  # noqa: E402
from mcp.server.elicitation import render_elicitation_schema  # noqa: E402
from mcp.server.mcpserver.exceptions import ResourceError, ToolError  # noqa: E402

from hiraku.cli import main  # noqa: E402
from hiraku.errors import ValidationError  # noqa: E402
from hiraku.mcp import (  # noqa: E402
    WorkflowApproval,
    create_server,
    register_tools,
    register_workflow_tools,
)
from hiraku.project import Project  # noqa: E402
from hiraku.record import Record  # noqa: E402


def load_record(record_id: str, *, project: Project) -> Record:
    return project.vault.get(record_id)


def list_records(*, project: Project) -> list[Record]:
    return project.vault.list()


def test_registration_exposes_described_tools(tmp_path: Path, monkeypatch) -> None:
    from mcp.server.mcpserver import MCPServer

    project = Project.create(tmp_path, install_skill=False)
    server = MCPServer("test")
    register_tools(server, project)
    register_workflow_tools(server, project)

    async def run_inline(func, *args, **kwargs):
        return func(*args)

    monkeypatch.setattr(anyio.to_thread, "run_sync", run_inline)

    async def exercise() -> None:
        tools = {tool.name: tool for tool in await server.list_tools()}
        assert {"project_info", "record_list", "runs", "workflow_start"} <= tools.keys()
        assert tools["project_info"].title == "Project information"
        assert tools["record_list"].annotations.read_only_hint is True
        assert tools["workflow_start"].title == "Start workflow"
        assert tools["workflow_start"].description
        result = await server.call_tool("project_info", {})
        assert result.structured_content["tracking_backend"] == "csv"

    asyncio.run(exercise())


from hiraku.workflows.langgraph import resume_workflow as _resume_workflow  # noqa: E402
from hiraku.workflows.langgraph import (  # noqa: E402
    start_workflow,
    workflow_mermaid,
    workflow_status,
)


def test_tools_and_validation(tmp_path: Path) -> None:
    config = Project.create(tmp_path)
    server = create_server(config)

    async def exercise() -> None:
        async def call(tool_name: str, **args):
            return await server.call_tool(tool_name, args)

        assert "record_reparent" not in {
            tool.name for tool in await server.list_tools()
        }
        await call(
            "record_create",
            name="root",
            fields={
                "type": "task",
                "status": "new",
            },
        )
        await call(
            "record_create", name="child", parent_id="root", fields={"type": "task"}
        )
        await call("record_update", record_id="child", fields={"status": "done"})
        assert load_record("child", project=config).status == "done"
        for name, args in [
            (
                "record_create",
                {"name": "bad", "parent_id": "missing", "fields": {"type": "task"}},
            ),
            ("record_create", {"name": "  ", "fields": {"type": "task"}}),
            ("record_update", {"record_id": "child", "fields": {"id": "renamed"}}),
            ("record_update", {"record_id": "child", "fields": {"related_to": ["no"]}}),
            ("record_delete", {"record_id": "root"}),
        ]:
            with pytest.raises(ToolError):
                await server.call_tool(name, args)
        assert load_record("child", project=config).related_to == []
        await call("record_delete", record_id="child")
        await call(
            "record_create",
            name="sources",
            fields={"type": "context"},
            markdown="Evidence",
        )
        with pytest.raises(ToolError):
            await call("record_create", name="  ", fields={"type": "task"})
        await call("record_update", record_id="sources", fields={}, markdown="Updated")
        assert load_record("sources", project=config).markdown == "Updated"
        await call("record_show", record_id="sources")
        await call("record_list")
        await call("record_show", record_id="root")
        await call("record_list")
        await call("record_delete", record_id="sources")
        assert "sources" not in {record.name for record in list_records(project=config)}

    asyncio.run(exercise())


def test_record_update_uses_vault_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = Project.create(tmp_path, install_skill=False)
    record = project.vault.create(
        "original", type="task", status="new", markdown="Before"
    )
    other = project.vault.create("other", type="task")
    server = create_server(project)

    # Exercise MCP routing without relying on the SDK's worker-thread scheduler.
    async def run_inline(func, *args, **kwargs):
        return func(*args)

    monkeypatch.setattr(anyio.to_thread, "run_sync", run_inline)

    async def exercise() -> None:
        result = await server.call_tool(
            "record_update",
            {
                "record_id": record.id,
                "fields": {"related_to": [other.id]},
                "markdown": "After",
            },
        )
        assert result.structured_content["name"] == "original"
        updated = project.vault.get(record.id)
        assert updated.path == project.records_dir / "original/original.md"
        assert updated.status == "new"
        assert updated.markdown == "After"
        assert updated.related_to == [other.id]

        for fields in ({"related_to": ["missing"]}, {"name": "renamed"}):
            with pytest.raises(ToolError):
                await server.call_tool(
                    "record_update", {"record_id": record.id, "fields": fields}
                )
        assert project.vault.get(record.id).name == "original"
        assert project.vault.get(record.id).related_to == [other.id]

    asyncio.run(exercise())


def test_resources_and_prompt(tmp_path: Path) -> None:
    config = Project.create(tmp_path)
    server = create_server(config)

    async def exercise() -> None:
        await server.call_tool(
            "record_create", {"name": "demo", "fields": {"type": "task"}}
        )
        await server.call_tool(
            "record_create",
            {
                "name": "literature",
                "fields": {"type": "context"},
                "markdown": "Curated evidence",
            },
        )
        literature = load_record("literature", project=config)
        folder = literature.path.parent
        (folder / "papers").mkdir()
        pdf = b"%PDF-1.4\nsource bytes"
        (folder / "papers" / "paper one.pdf").write_bytes(pdf)
        outside = tmp_path / "private.txt"
        outside.write_text("secret")
        (folder / "outside.txt").symlink_to(outside)
        catalog = list(await server.read_resource("hiraku://catalog"))[0]
        uris = json.loads(catalog.content)
        assert f"hiraku://sources/{literature.id}/papers%2Fpaper%20one.pdf" in uris
        assert "hiraku://sources/literature/outside.txt" not in uris
        content = list(
            await server.read_resource(
                "hiraku://sources/literature/papers%2Fpaper%20one.pdf"
            )
        )[0]
        assert content.content == pdf
        assert content.mime_type == "application/pdf"
        for path in ["outside.txt", "%2E%2E%2F%2E%2E%2Fprivate.txt", "%2Fetc%2Fpasswd"]:
            # Encoded traversal may already fail to match the URI template.
            with pytest.raises(
                ResourceError, match="Source must be a file|Unknown resource"
            ):
                await server.read_resource(f"hiraku://sources/literature/{path}")
        for uri in [
            "hiraku://records/demo",
            "hiraku://records/literature",
        ]:
            assert list(await server.read_resource(uri))[0].content
        with pytest.raises(ResourceError, match="'missing' not found"):
            await server.read_resource("hiraku://records/missing")
        prompt = await server.get_prompt(
            "propose_implementation",
            {
                "record_id": "demo",
                "related_record_ids": "literature",
            },
        )
        assert "Curated evidence" in prompt.messages[0].content.text

    asyncio.run(exercise())


def test_execute_tool_runs_in_foreground(tmp_path: Path) -> None:
    config = Project.create(tmp_path)
    server = create_server(config)

    async def exercise() -> None:
        await server.call_tool(
            "record_create", {"name": "demo", "fields": {"type": "task"}}
        )
        result = await server.call_tool(
            "execute",
            {
                "record_id": "demo",
                "command": [
                    sys.executable,
                    "-c",
                    "import sys; print('out'); print('err', file=sys.stderr); "
                    "raise SystemExit(7)",
                ],
            },
        )
        assert result.structured_content == {
            "returncode": 7,
            "stdout": "out\n",
            "stderr": "err\n",
        }

    asyncio.run(exercise())


def test_runs(tmp_path: Path) -> None:
    config = Project.create(tmp_path)
    (tmp_path / "runs.csv").write_text(
        "id,record_id,metric.acc\na,task,0.5\nb,task,0.9\nc,other,0.7\n"
    )
    server = create_server(config)

    async def exercise() -> None:
        await server.call_tool(
            "record_create",
            {
                "name": "task",
                "fields": {
                    "type": "task",
                    "metrics": [{"name": "acc", "direction": "maximize"}],
                },
            },
        )

        async def ids(**args) -> list[str]:
            result = await server.call_tool("runs", {"record_id": "task", **args})
            return [run["id"] for run in result.structured_content["result"]]

        assert await ids(sort="acc") == ["b", "a"]
        assert await ids(sort="acc", ascending=True, limit=1) == ["a"]
        assert sorted(await ids(all_records=True)) == ["a", "b", "c"]
        with pytest.raises(ToolError, match="requires sort"):
            await server.call_tool("runs", {"record_id": "task", "ascending": True})

    asyncio.run(exercise())


def test_stdio_session(tmp_path: Path) -> None:
    config = Project.create(tmp_path)
    source = config.records_dir / "papers"

    async def exercise() -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-c",
                "from hiraku.cli import main; main()",
                "mcp",
            ],
            cwd=str(tmp_path),
        )
        async with stdio_client(params) as (read, write):  # noqa: SIM117
            async with ClientSession(read, write, read_timeout_seconds=10) as client:
                await client.initialize()
                tools = await client.list_tools()
                assert "record_create" in {tool.name for tool in tools.tools}
                assert (
                    str((await client.list_resources()).resources[0].uri)
                    == "hiraku://catalog"
                )
                assert (
                    len((await client.list_resource_templates()).resource_templates)
                    == 4
                )
                assert (await client.list_prompts()).prompts[
                    0
                ].name == "propose_implementation"
                assert not (
                    await client.call_tool(
                        "record_create",
                        {
                            "name": "papers",
                            "fields": {"type": "context"},
                            "markdown": "Literature",
                        },
                    )
                ).is_error
                source.mkdir(exist_ok=True)
                pdf = b"%PDF-test"
                (source / "paper.pdf").write_bytes(pdf)
                result = await client.read_resource("hiraku://sources/papers/paper.pdf")
                assert result.contents[0].mime_type == "application/pdf"
                assert base64.b64decode(result.contents[0].blob) == pdf
                error = await client.call_tool("record_show", {"record_id": "missing"})
                assert error.is_error
                assert "missing" in error.content[0].text

    asyncio.run(exercise())


def test_cli_project_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["mcp"])
    assert result.exit_code == 1
    assert "No hiraku.yml" in result.output


def test_cli_serves_project(tmp_path, monkeypatch):
    from unittest.mock import Mock

    config = Project.create(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    factory = Mock()
    monkeypatch.setattr("hiraku.mcp.create_server", factory)
    result = CliRunner().invoke(main, ["mcp"])
    assert result.exit_code == 0, result.output
    assert factory.call_args.kwargs == {}
    assert factory.call_args.args[0].path == config.path
    factory.return_value.run.assert_called_once_with(transport="stdio")


def test_workflow_mcp_uses_generic_langgraph_controls(tmp_path) -> None:
    config = Project.create(tmp_path, example="workflows", install_skill=False)
    server = create_server(config)

    tools = asyncio.run(server.list_tools())
    names = {tool.name for tool in tools}
    assert {
        "workflow_list",
        "workflow_start",
        "workflow_status",
        "workflow_resume",
        "workflow_approval",
    } <= names
    assert (
        not {
            "workflow_send_event",
            "session_list",
            "session_show",
            "session_cancel",
            "count-steps",
            "greet",
            "scheduler",
        }
        & names
    )


def resume_workflow(session_id, response, *, project):
    status = workflow_status(session_id, project=project)
    return _resume_workflow(
        session_id, response, project=project, checkpoint_id=status["checkpoint_id"]
    )


def _project(tmp_path) -> Project:
    return Project.create(tmp_path, example="workflows", install_skill=False)


def _tool_names(server: Any) -> set[str]:
    async def list_tools() -> set[str]:
        return {tool.name for tool in await server.list_tools()}

    return anyio.run(list_tools)


def _interrupt(result: dict[str, Any]) -> dict[str, Any]:
    assert result["status"] == "waiting"
    assert len(result["interrupts"]) == 1
    return result["interrupts"][0]["value"]


def test_workflow_approval_schema_omits_codex_unsupported_root_title() -> None:
    schema = render_elicitation_schema(WorkflowApproval)
    assert set(schema) <= {"type", "properties", "required", "$schema"}
    assert schema["properties"]["decision"]["enum"] == [
        "approve",
        "revise",
        "stop",
    ]
    assert set(schema["properties"]) == {"decision", "feedback"}


def test_count_steps_mcp_tools_and_graph_loop(tmp_path) -> None:
    config = _project(tmp_path)
    server = create_server(config)
    assert {"workflow_start", "workflow_status", "workflow_resume"}.issubset(
        _tool_names(server)
    )
    assert "workflow_approval" in _tool_names(server)

    started = start_workflow("count-steps", {"count": 2}, project=config)
    first = _interrupt(started)
    assert "Step 1/2: token" in first["instruction"]
    assert first["data"]["step"] == 1
    assert first["response_schema"]["type"] == "object"
    assert first["response_schema"]["additionalProperties"] is False
    assert first["response_schema"]["properties"]["decision"]["enum"] == [
        "continue",
        "finish",
    ]

    wrong = resume_workflow(
        started["workflow_session_id"],
        {"token": "wrong", "decision": "continue"},
        project=config,
    )
    retry = _interrupt(wrong)
    assert retry["data"] == first["data"]
    assert "does not match" in retry["error"]

    invalid_decision = resume_workflow(
        started["workflow_session_id"],
        {"token": first["data"]["token"], "decision": "skip"},
        project=config,
    )
    retry = _interrupt(invalid_decision)
    assert retry["data"] == first["data"]
    assert "continue" in retry["error"] and "finish" in retry["error"]

    next_step = resume_workflow(
        started["workflow_session_id"],
        {"token": first["data"]["token"], "decision": "continue"},
        project=config,
    )
    second = _interrupt(next_step)
    assert "Step 2/2: token" in second["instruction"]
    assert second["data"]["token"] != first["data"]["token"]

    done = resume_workflow(
        started["workflow_session_id"],
        {"token": second["data"]["token"], "decision": "continue"},
        project=config,
    )
    assert done["status"] == "completed"
    assert "Finished after 2 of 2 steps." in done["result"]
    assert workflow_status(started["workflow_session_id"], project=config) == done


def test_count_steps_can_finish_early_and_resume_after_server_restart(tmp_path) -> None:
    config = _project(tmp_path)
    server = create_server(config)
    assert "workflow_resume" in _tool_names(server)

    started = start_workflow("count-steps", {}, project=config)
    first = _interrupt(started)
    assert first["data"]["count"] == 5

    # Recreate the server to verify that the run lives in LangGraph's SQLite state.
    server = create_server(config)
    assert "workflow_status" in _tool_names(server)
    completed = resume_workflow(
        started["workflow_session_id"],
        {"token": first["data"]["token"], "decision": "finish"},
        project=config,
    )
    assert completed["status"] == "completed"
    assert "Finished after 1 of 5 steps." in completed["result"]


def test_count_steps_rejects_invalid_arguments(tmp_path) -> None:
    config = _project(tmp_path)
    with pytest.raises(ValidationError, match="positive integer"):
        start_workflow("count-steps", {"count": 0}, project=config)


def test_mcp_exposes_only_the_langgraph_workflow_interface(tmp_path) -> None:
    config = _project(tmp_path)
    tools = _tool_names(create_server(config))
    assert {
        "workflow_list",
        "workflow_start",
        "workflow_status",
        "workflow_resume",
        "workflow_approval",
    } <= tools
    assert (
        not {
            "workflow_send_event",
            "session_list",
            "session_show",
            "session_cancel",
            "count-steps",
            "greet",
            "scheduler",
        }
        & tools
    )


def test_workflow_graph_is_available_from_cli_and_mcp(tmp_path, monkeypatch) -> None:
    config = _project(tmp_path)
    server = create_server(config)

    monkeypatch.chdir(tmp_path)
    cli_result = CliRunner().invoke(main, ["workflow", "graph", "count-steps"])
    assert cli_result.exit_code == 0, cli_result.output
    assert "graph TD" in cli_result.output
    assert "harness_step" in cli_result.output

    templates = anyio.run(server.list_resource_templates)
    assert "hiraku://workflows/{workflow_id}/graph" in {
        template.uri_template for template in templates
    }
    mermaid = workflow_mermaid("count-steps", project=config)
    assert "graph TD" in mermaid
    assert "harness_step" in mermaid


def test_collaborative_workflow_is_a_checkpointed_langgraph(tmp_path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    Record.create(
        "task",
        type="task",
        project=config,
    )
    started = start_workflow(
        "collaborative",
        {"rounds": 1, "command": ["python", "train.py"]},
        project=config,
    )
    assert started["status"] == "waiting"
    interrupt = _interrupt(started)
    assert interrupt["step"] == "draft"
    assert interrupt["data"]["task"] == config.vault.get("task").id

    bypass = resume_workflow(
        started["workflow_session_id"], {"event": "finish"}, project=config
    )
    assert bypass["status"] == "waiting"
    assert "approval decision is required" in _interrupt(bypass)["error"]
    invalid = resume_workflow(
        started["workflow_session_id"], {"approval": {"decision": []}}, project=config
    )
    assert "approval decision is required" in _interrupt(invalid)["error"]
    revised = resume_workflow(
        started["workflow_session_id"],
        {"approval": {"decision": "revise", "feedback": "Use a smaller model."}},
        project=config,
    )
    assert _interrupt(revised)["feedback"] == "Use a smaller model."
    completed = resume_workflow(
        started["workflow_session_id"],
        {"approval": {"decision": "stop"}},
        project=config,
    )
    assert completed["status"] == "completed"
    assert "results" in completed["result"]


def test_collaborative_approval_uses_mcp_elicitation(tmp_path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    Record.create(
        "task",
        type="task",
        project=config,
    )
    started = start_workflow(
        "collaborative",
        {"rounds": 1, "command": ["python", "train.py"]},
        project=config,
    )
    requested = []

    async def elicit(_context, params):
        requested.append(params)
        return types.ElicitResult(
            action="accept",
            content={"decision": "approve"},
        )

    async def exercise() -> tuple[Any, Any]:
        async with Client(
            create_server(config),
            elicitation_callback=elicit,
        ) as client:
            approval = await client.call_tool(
                "workflow_approval",
                {
                    "workflow_session_id": started["workflow_session_id"],
                    "interrupt_id": started["interrupts"][0]["id"],
                    "proposal": "Try a larger learning rate.",
                    "response": {"event": "finish"},
                },
            )
            resumed = await client.call_tool(
                "workflow_resume",
                {
                    "workflow_session_id": started["workflow_session_id"],
                    "response": approval.structured_content,
                    "checkpoint_id": started["checkpoint_id"],
                },
            )
            return approval, resumed

    result, resumed = anyio.run(exercise)
    assert resumed.structured_content["status"] == "completed"
    assert len(requested) == 1
    assert "draft" in requested[0].message
    assert "larger learning rate" in requested[0].message
    schema = requested[0].requested_schema
    assert set(schema) <= {"type", "properties", "required", "$schema"}
    assert schema["properties"]["decision"]["enum"] == [
        "approve",
        "revise",
        "stop",
    ]
    assert set(schema["properties"]) == {"decision", "feedback"}
    assert result.structured_content == {
        "approval": {"decision": "approve"},
        "response": {"event": "finish"},
    }


def test_cancelled_collaborative_approval_leaves_run_waiting(tmp_path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    Record.create(
        "task",
        type="task",
        project=config,
    )
    started = start_workflow(
        "collaborative",
        {"rounds": 1, "command": ["python", "train.py"]},
        project=config,
    )

    async def elicit(_context, _params):
        return types.ElicitResult(action="cancel")

    async def exercise() -> tuple[Any, Any]:
        async with Client(
            create_server(config),
            elicitation_callback=elicit,
        ) as client:
            approval = await client.call_tool(
                "workflow_approval",
                {
                    "workflow_session_id": started["workflow_session_id"],
                    "interrupt_id": started["interrupts"][0]["id"],
                    "proposal": "Try a larger learning rate.",
                    "response": {"event": "finish"},
                },
            )
            status = await client.call_tool(
                "workflow_status",
                {"workflow_session_id": started["workflow_session_id"]},
            )
            return approval, status

    approval, status = anyio.run(exercise)
    assert approval.structured_content == {"decision": "cancel"}
    assert status.structured_content["status"] == "waiting"


def test_mcp_approval_supports_other_workflows(tmp_path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    directory = config.workflows_dir / "review"
    directory.mkdir()
    (directory / "workflow.py").write_text(
        "from langgraph.graph import START, END, StateGraph\n"
        "from langgraph.types import interrupt\n"
        "from typing import TypedDict\n"
        "class State(TypedDict, total=False):\n"
        "    run_id: str\n"
        "    final: str\n"
        "def input_schema():\n"
        "    return {\n"
        "        'type': 'object',\n"
        "        'properties': {},\n"
        "        'additionalProperties': False,\n"
        "    }\n"
        "def build_graph(checkpointer, *, project):\n"
        "    def review(state):\n"
        "        answer = interrupt({'approval': {'message': 'Review this'}})\n"
        "        return {'final': answer['response']['value']}\n"
        "    graph = StateGraph(State)\n"
        "    graph.add_node('review', review)\n"
        "    graph.add_edge(START, 'review')\n"
        "    graph.add_edge('review', END)\n"
        "    return graph.compile(checkpointer=checkpointer)\n",
        encoding="utf-8",
    )
    started = start_workflow("review", project=config)

    async def elicit(_context, _params):
        return types.ElicitResult(action="accept", content={"decision": "approve"})

    async def exercise():
        async with Client(create_server(config), elicitation_callback=elicit) as client:
            approval = await client.call_tool(
                "workflow_approval",
                {
                    "workflow_session_id": started["workflow_session_id"],
                    "interrupt_id": started["interrupts"][0]["id"],
                    "proposal": "Accept the value",
                    "response": {"value": "approved"},
                },
            )
            return await client.call_tool(
                "workflow_resume",
                {
                    "workflow_session_id": started["workflow_session_id"],
                    "response": approval.structured_content,
                    "checkpoint_id": started["checkpoint_id"],
                },
            )

    result = anyio.run(exercise)
    assert result.structured_content["result"] == "approved"


def test_approval_result_can_resume_after_server_restart(tmp_path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    Record.create(
        "task",
        type="task",
        project=config,
    )
    first = start_workflow(
        "collaborative",
        {"rounds": 1, "command": ["python", "train.py"]},
        project=config,
    )

    async def elicit(_context, _params):
        return types.ElicitResult(action="accept", content={"decision": "stop"})

    async def exercise():
        async with Client(create_server(config), elicitation_callback=elicit) as client:
            approval = await client.call_tool(
                "workflow_approval",
                {
                    "workflow_session_id": first["workflow_session_id"],
                    "interrupt_id": first["interrupts"][0]["id"],
                    "proposal": "Stop the workflow",
                    "response": {},
                },
            )
        async with Client(create_server(config)) as client:
            return await client.call_tool(
                "workflow_resume",
                {
                    "workflow_session_id": first["workflow_session_id"],
                    "response": approval.structured_content,
                    "checkpoint_id": first["checkpoint_id"],
                },
            )

    resumed = anyio.run(exercise)
    assert resumed.structured_content["status"] == "completed"
