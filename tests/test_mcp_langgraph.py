from __future__ import annotations

from typing import Any

import anyio
import pytest
from click.testing import CliRunner
from mcp import Client, types
from mcp.server.elicitation import render_elicitation_schema

from hiraku.cli import main
from hiraku.errors import ValidationError
from hiraku.mcp import WorkflowApproval, create_server
from hiraku.project import Project
from hiraku.record import Record
from hiraku.workflows.langgraph import resume_workflow as _resume_workflow
from hiraku.workflows.langgraph import (
    start_workflow,
    workflow_mermaid,
    workflow_status,
)


def resume_workflow(session_id, response, *, config):
    status = workflow_status(session_id, project=config)
    return _resume_workflow(
        session_id, response, project=config, checkpoint_id=status["checkpoint_id"]
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
    assert interrupt["data"]["task"] == "task"

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
