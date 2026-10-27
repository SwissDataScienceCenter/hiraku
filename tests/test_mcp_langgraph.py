from __future__ import annotations

from typing import Any

import anyio
import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.errors import ValidationError
from hiraku.mcp import create_server
from hiraku.record import Record
from hiraku.workflows.langgraph import (
    resume_workflow,
    start_workflow,
    workflow_mermaid,
    workflow_status,
)


def _project(tmp_path) -> HirakuConfig:
    return HirakuConfig.init(tmp_path, example="workflows", install_skill=False)


def _tool_names(server: Any) -> set[str]:
    async def list_tools() -> set[str]:
        return {tool.name for tool in await server.list_tools()}

    return anyio.run(list_tools)


def _interrupt(result: dict[str, Any]) -> dict[str, Any]:
    assert result["status"] == "waiting"
    assert len(result["interrupts"]) == 1
    return result["interrupts"][0]["value"]


def test_count_steps_mcp_tools_and_graph_loop(tmp_path) -> None:
    config = _project(tmp_path)
    server = create_server(config)
    assert {"workflow_start", "workflow_status", "workflow_resume"}.issubset(
        _tool_names(server)
    )

    started = start_workflow("count-steps", {"count": 2}, config=config)
    first = _interrupt(started)
    assert "Step 1/2: token" in first["instruction"]
    assert first["data"]["step"] == 1
    assert first["response_schema"]["decision"] == "continue or finish"

    wrong = resume_workflow(
        started["run_id"],
        {"token": "wrong", "decision": "continue"},
        config=config,
    )
    retry = _interrupt(wrong)
    assert retry["data"] == first["data"]
    assert "does not match" in retry["error"]

    invalid_decision = resume_workflow(
        started["run_id"],
        {"token": first["data"]["token"], "decision": "skip"},
        config=config,
    )
    retry = _interrupt(invalid_decision)
    assert retry["data"] == first["data"]
    assert "Decision must be" in retry["error"]

    next_step = resume_workflow(
        started["run_id"],
        {"token": first["data"]["token"], "decision": "continue"},
        config=config,
    )
    second = _interrupt(next_step)
    assert "Step 2/2: token" in second["instruction"]
    assert second["data"]["token"] != first["data"]["token"]

    done = resume_workflow(
        started["run_id"],
        {"token": second["data"]["token"], "decision": "continue"},
        config=config,
    )
    assert done["status"] == "completed"
    assert "Finished after 2 of 2 steps." in done["result"]
    assert workflow_status(started["run_id"], config=config) == done


def test_count_steps_can_finish_early_and_resume_after_server_restart(tmp_path) -> None:
    config = _project(tmp_path)
    server = create_server(config)
    assert "workflow_resume" in _tool_names(server)

    started = start_workflow("count-steps", {}, config=config)
    first = _interrupt(started)
    assert first["data"]["count"] == 5

    # Recreate the server to verify that the run lives in LangGraph's SQLite state.
    server = create_server(config)
    assert "workflow_status" in _tool_names(server)
    completed = resume_workflow(
        started["run_id"],
        {"token": first["data"]["token"], "decision": "finish"},
        config=config,
    )
    assert completed["status"] == "completed"
    assert "Finished after 1 of 5 steps." in completed["result"]


def test_count_steps_rejects_invalid_arguments(tmp_path) -> None:
    config = _project(tmp_path)
    with pytest.raises(ValidationError, match="positive integer"):
        start_workflow("count-steps", {"count": 0}, config=config)


def test_mcp_exposes_only_the_langgraph_workflow_interface(tmp_path) -> None:
    config = _project(tmp_path)
    tools = _tool_names(create_server(config))
    assert {
        "workflow_list",
        "workflow_start",
        "workflow_status",
        "workflow_resume",
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
    mermaid = workflow_mermaid("count-steps", config=config)
    assert "graph TD" in mermaid
    assert "harness_step" in mermaid


def test_collaborative_workflow_is_a_checkpointed_langgraph(tmp_path) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    Record.create(
        "task",
        type="task",
        config=config,
    )
    started = start_workflow(
        "collaborative",
        {"rounds": 1, "command": ["python", "train.py"]},
        config=config,
    )
    assert started["status"] == "waiting"
    interrupt = _interrupt(started)
    assert interrupt["step"] == "draft"
    assert interrupt["data"]["task"] == "task"

    completed = resume_workflow(started["run_id"], {"event": "finish"}, config=config)
    assert completed["status"] == "completed"
    assert "results" in completed["result"]
