from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.project import Project
from hiraku.workflows.langgraph import resume_workflow as _resume_workflow
from hiraku.workflows.langgraph import start_workflow, workflow_status


def resume_workflow(session_id, response, *, project):
    status = workflow_status(session_id, project=project)
    return _resume_workflow(
        session_id, response, project=project, checkpoint_id=status["checkpoint_id"]
    )


def invoke(runner: CliRunner, arguments: list[str], payload: Any = None) -> Any:
    result = runner.invoke(
        main, arguments, input=None if payload is None else json.dumps(payload)
    )
    assert result.exit_code == 0, result.output
    return result


def start(runner: CliRunner, workflow: str, payload: dict[str, Any]) -> dict[str, Any]:
    result = invoke(
        runner, ["workflow", "start", workflow, "--arguments-file", "-"], payload
    )
    response = json.loads(result.output)
    return response


@pytest.fixture
def runner(tmp_path: Path, monkeypatch) -> CliRunner:
    project = tmp_path / "workflows"
    project.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=project, check=True)
    monkeypatch.chdir(project)
    selected = CliRunner()
    invoke(selected, ["example", "create", "workflows", "."])
    workflows = project / "hiraku" / "workflows"
    assert (workflows / "count-steps" / "workflow.py").is_file()
    assert (workflows / "greet" / "workflow.py").is_file()
    assert (workflows / "scheduler" / "workflow.py").is_file()
    assert not (project / "example.yml").exists()
    return selected


def test_all_examples_use_the_langgraph_cli(
    runner: CliRunner,
) -> None:
    listed = invoke(runner, ["workflow", "list"]).output
    assert "count-steps" in listed
    assert "greet" in listed
    assert "scheduler" in listed


def test_greet_is_one_final_prompt_filled_from_its_arguments(
    runner: CliRunner,
) -> None:
    config = Project.discover()
    response = start_workflow("greet", {"name": "Ada", "times": 3}, project=config)
    assert response["status"] == "completed"
    assert response["workflow_id"] == "greet"
    assert response["result"].startswith("# Greet\n\nPrint 3 greetings to Ada")
    assert "Greet formally:\nfalse." in response["result"]

    default = start_workflow("greet", {}, project=config)
    assert default["result"].startswith("# Greet\n\nPrint 2 greetings to World")
    assert "Greet formally:\nfalse." in default["result"]


def test_cli_uses_workflow_session_ids_for_resume_and_status(runner: CliRunner) -> None:
    started = json.loads(
        invoke(
            runner,
            ["workflow", "start", "count-steps", "--arguments-file", "-"],
            {"count": 1},
        ).output
    )
    assert started["status"] == "waiting"
    workflow_session_id = started["workflow_session_id"]
    assert (
        json.loads(invoke(runner, ["workflow", "status", workflow_session_id]).output)
        == started
    )

    interrupt = started["interrupts"][0]["value"]
    completed = json.loads(
        invoke(
            runner,
            [
                "workflow",
                "resume",
                workflow_session_id,
                "--checkpoint-id",
                started["checkpoint_id"],
                "--arguments-file",
                "-",
            ],
            {"token": interrupt["data"]["token"], "decision": "continue"},
        ).output
    )
    assert completed["status"] == "completed"
    assert completed["workflow_id"] == "count-steps"


def children(parent: str) -> list[Any]:
    config = Project.discover()
    return sorted(
        config.vault.children(parent),
        key=lambda item: item.id,
    )


def set_count(runner: CliRunner, record_id: str, count: int) -> None:
    invoke(runner, ["record", "update", record_id, "--set", f"count={count}"])


def test_scheduler_langgraph_works_by_priority_until_every_record_reaches_target(
    runner: CliRunner,
) -> None:
    config = Project.discover()
    response = start_workflow("scheduler", {"records": 3, "target": 2}, project=config)
    interruption = response["interrupts"][0]["value"]
    parent = interruption["data"]["parent"]
    records = children(parent)
    assert len(records) == 3
    assert {item.id: item.title for item in records} == {
        f"{parent}/counter-{index}": f"Counter {index}" for index in range(1, 4)
    }
    assert config.vault.load(parent).title == "Counter scheduling"
    assert all(1 <= item.priority <= 9 and item.count == 0 for item in records)
    # The highest priority runs to the target first; ties go to the larger ID.
    order = sorted(records, key=lambda item: (item.priority, item.id), reverse=True)
    expected = [item.id for item in order for _ in range(2)]

    for done, record_id in enumerate(expected):
        interruption = response["interrupts"][0]["value"]
        context = interruption["data"]
        assert context["record"] == record_id
        assert context["remaining"] == 6 - done
        count = context["count"]
        assert f"--set count={count + 1}" in interruption["instruction"]
        other = next(item.id for item in records if item.id != record_id)
        wrong = resume_workflow(
            response["workflow_session_id"], {"record_id": other}, project=config
        )
        retry = wrong["interrupts"][0]["value"]
        assert retry["data"] == context
        assert "assigned record" in retry["error"]

        unchanged = resume_workflow(
            response["workflow_session_id"],
            {"record_id": record_id},
            project=config,
        )
        retry = unchanged["interrupts"][0]["value"]
        assert f"set it to {count + 1}" in retry["error"]

        set_count(runner, record_id, count + 1)
        response = resume_workflow(
            response["workflow_session_id"],
            {"record_id": record_id},
            project=config,
        )

    assert response["status"] == "completed"
    assert response["workflow_id"] == "scheduler"
    assert workflow_status(response["workflow_session_id"], project=config) == response
    assert all(item.id in response["result"] for item in records)


def test_langgraph_runs_are_routed_to_their_own_workflow(runner: CliRunner) -> None:
    config = Project.discover()
    count_run = start_workflow("count-steps", {"count": 1}, project=config)
    scheduler_run = start_workflow("scheduler", {"records": 1}, project=config)

    assert (
        workflow_status(count_run["workflow_session_id"], project=config)["workflow_id"]
        == "count-steps"
    )
    assert (
        workflow_status(scheduler_run["workflow_session_id"], project=config)[
            "workflow_id"
        ]
        == "scheduler"
    )
