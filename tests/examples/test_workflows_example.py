from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.project import Project
from hiraku.tracking.leaderboard import Leaderboard
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
    assert "research" in listed


def test_research_setup_accepts_project_context(runner: CliRunner) -> None:
    project = Project.discover()
    started = start_workflow(
        "research", {"command": ["python", "train.py"]}, project=project
    )
    assert started["status"] == "waiting"
    assert started["interrupts"][0]["value"]["step"] == "setup"

    project.vault.create("project-setup", type="context")
    Leaderboard.create(project, "runs", {"type": "csv", "path": "runs.csv"})
    resumed = resume_workflow(
        started["workflow_session_id"], {"event": "set_up"}, project=project
    )
    assert resumed["status"] == "waiting"
    assert resumed["interrupts"][0]["value"]["step"] == "draft"

    task = project.vault.create(
        "task", type="task", metrics=[{"name": "loss", "direction": "minimize"}]
    )
    session = started["workflow_session_id"]
    designed = resume_workflow(session, {"record_id": task.id}, project=project)
    assert designed["interrupts"][0]["value"]["step"] == "design"

    implementation = resume_workflow(
        session,
        {
            "experiments": [
                {
                    "record_id": "baseline",
                    "summary": "Baseline",
                    "hypothesis": "Loss falls",
                    "expected": "Lower loss",
                    "change": "Train a model",
                }
            ]
        },
        project=project,
    )
    assert implementation["interrupts"][0]["value"]["step"] == "implement"
    experiment = project.vault.load("baseline")
    assert experiment.parent_id == task.id
    assert experiment.type == "experiment"
    assert experiment.id == "baseline"

    subprocess.run(["git", "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "baseline"], check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    launch = resume_workflow(session, {"commit": commit}, project=project)
    assert launch["interrupts"][0]["value"]["step"] == "launch"

    waiting = resume_workflow(session, {"event": "launched"}, project=project)
    assert waiting["interrupts"][0]["value"]["step"] == "wait"
    (project.root / "runs.csv").write_text(
        f"id,record_id,commit,metric.loss\nrun-1,{experiment.id},{commit},0.5\n"
    )
    analysis = resume_workflow(session, {"event": "checked"}, project=project)
    assert analysis["interrupts"][0]["value"]["step"] == "analyze"
    triage = resume_workflow(
        session,
        {"verdict": "supports", "conclusion": "run-1: loss 0.5"},
        project=project,
    )
    assert triage["interrupts"][0]["value"]["step"] == "triage"
    completed = resume_workflow(
        session, {"decision": "stop", "reason": "Done"}, project=project
    )
    assert completed["status"] == "completed"
    assert "run-1" in completed["result"]


def test_research_accepts_task_name(runner: CliRunner) -> None:
    project = Project.discover()
    project.vault.create("project-setup", type="context")
    Leaderboard.create(project, "runs", {"type": "csv", "path": "runs.csv"})
    task = project.vault.create(
        "task", type="task", metrics=[{"name": "loss", "direction": "minimize"}]
    )
    started = start_workflow(
        "research",
        {"task": "task", "command": ["python", "train.py"]},
        project=project,
    )
    interrupt = started["interrupts"][0]["value"]
    assert interrupt["step"] == "design"
    assert interrupt["data"]["task"] == task.id


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


def _value(response: dict[str, Any]) -> dict[str, Any]:
    return response["interrupts"][0]["value"]


def _commit(message: str) -> str:
    subprocess.run(["git", "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", message], check=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def _autonomous_study(**options: Any) -> tuple[Project, str, dict[str, Any]]:
    project = Project.discover()
    project.vault.create("project-setup", type="context")
    Leaderboard.create(project, "runs", {"type": "csv", "path": "runs.csv"})
    task = project.vault.create(
        "task", type="task", metrics=[{"name": "loss", "direction": "minimize"}]
    )
    started = start_workflow(
        "research",
        {
            "task": task.id,
            "command": ["python", "train.py"],
            "autonomous": True,
            **options,
        },
        project=project,
    )
    return project, started["workflow_session_id"], started


def _design(project: Project, session: str) -> dict[str, Any]:
    return resume_workflow(
        session,
        {
            "experiments": [
                {
                    "record_id": "baseline",
                    "summary": "Baseline",
                    "hypothesis": "Loss falls",
                    "expected": "Lower loss",
                    "change": "Train a model",
                }
            ]
        },
        project=project,
    )


def _approve_design(project: Project, session: str) -> dict[str, Any]:
    _design(project, session)
    return resume_workflow(
        session,
        {"decision": "approve", "findings": "Sound.", "sources": ["model knowledge"]},
        project=project,
    )


def _implement(
    project: Project, session: str, message: str
) -> tuple[dict[str, Any], str]:
    sha = _commit(message)
    response = resume_workflow(
        session,
        {
            "commit": sha,
            "smoke_command": "python train.py --steps 2",
            "smoke_output": "loss 1.0",
        },
        project=project,
    )
    return response, sha


def _to_code_review(project: Project, session: str) -> tuple[dict[str, Any], str]:
    _approve_design(project, session)
    return _implement(project, session, "baseline")


def _to_result_review(project: Project, session: str) -> str:
    response, sha = _to_code_review(project, session)
    assert _value(response)["step"] == "review_code"
    resume_workflow(
        session, {"decision": "approve", "findings": "Fine."}, project=project
    )
    resume_workflow(session, {"event": "launched"}, project=project)
    (project.root / "runs.csv").write_text(
        f"id,record_id,commit,metric.loss\nrun-1,baseline,{sha},0.5\n"
    )
    resume_workflow(session, {"event": "checked"}, project=project)
    analyzed = resume_workflow(
        session,
        {"verdict": "supports", "conclusion": "run-1: loss 0.5"},
        project=project,
    )
    assert _value(analyzed)["step"] == "review_result"
    return sha


def test_autonomous_study_reviews_design_code_and_result(runner: CliRunner) -> None:
    project, session, started = _autonomous_study()
    assert _value(started)["step"] == "design"
    assert "do not wait for the user" in _value(started)["instruction"]

    reviewing = _design(project, session)
    assert _value(reviewing)["step"] == "review_design"
    implementing = resume_workflow(
        session,
        {"decision": "approve", "findings": "Sound.", "sources": ["model knowledge"]},
        project=project,
    )
    assert _value(implementing)["step"] == "implement"
    assert _value(implementing)["data"]["smoke_test_required"] is True

    sha = _commit("baseline")
    missing = resume_workflow(session, {"commit": sha}, project=project)
    assert _value(missing)["step"] == "implement"
    assert "smoke test" in _value(missing)["feedback"]
    code = resume_workflow(
        session,
        {"commit": sha, "smoke_command": "python train.py", "smoke_output": "ok"},
        project=project,
    )
    assert _value(code)["step"] == "review_code"
    assert _value(code)["data"]["diff"].endswith(f"{sha}")
    launch = resume_workflow(
        session, {"decision": "approve", "findings": "Fine."}, project=project
    )
    assert _value(launch)["step"] == "launch"
    resume_workflow(session, {"event": "launched"}, project=project)
    (project.root / "runs.csv").write_text(
        f"id,record_id,commit,metric.loss\nrun-1,baseline,{sha},0.5\n"
    )
    resume_workflow(session, {"event": "checked"}, project=project)
    result = resume_workflow(
        session,
        {"verdict": "supports", "conclusion": "run-1: loss 0.5"},
        project=project,
    )
    assert _value(result)["step"] == "review_result"
    triage = resume_workflow(
        session,
        {
            "own_verdict": "supports",
            "decision": "approve",
            "findings": "Holds.",
            "sources": ["model knowledge"],
        },
        project=project,
    )
    assert _value(triage)["step"] == "triage"
    completed = resume_workflow(
        session, {"decision": "stop", "reason": "Done"}, project=project
    )
    assert completed["status"] == "completed"
    experiment = project.vault.load("baseline")
    assert [item["kind"] for item in experiment.reviews] == [
        "design",
        "code",
        "result",
    ]


def test_rejected_design_is_redesigned_then_kept_after_the_limit(
    runner: CliRunner,
) -> None:
    project, session, _ = _autonomous_study(review_rounds=1)
    _design(project, session)
    revise = {"decision": "revise", "findings": "No control.", "sources": ["paper"]}
    redesign = resume_workflow(session, revise, project=project)
    assert _value(redesign)["step"] == "design"
    assert _value(redesign)["data"]["review_feedback"] == "No control."
    assert "baseline" not in {record.id for record in project.vault.list()}

    _design(project, session)
    implementing = resume_workflow(session, revise, project=project)
    assert _value(implementing)["step"] == "implement"
    assert project.vault.load("baseline").design_review == "unresolved"


def test_code_review_sends_back_then_concludes_after_the_limit(
    runner: CliRunner,
) -> None:
    project, session, _ = _autonomous_study(review_rounds=1)
    code, _ = _to_code_review(project, session)
    assert _value(code)["step"] == "review_code"
    fix = {"decision": "fix", "findings": "Leaks evaluation data."}
    implementing = resume_workflow(session, fix, project=project)
    assert _value(implementing)["step"] == "implement"
    assert _value(implementing)["data"]["review_feedback"] == "Leaks evaluation data."
    assert project.vault.load("baseline").commit is None

    code, _ = _implement(project, session, "baseline again")
    assert _value(code)["step"] == "review_code"
    triage = resume_workflow(session, fix, project=project)
    assert _value(triage)["step"] == "triage"
    experiment = project.vault.load("baseline")
    assert experiment.verdict == "inconclusive"
    assert experiment.conclusion.startswith("Did not pass the code review")


def test_result_review_reanalyzes_or_sends_code_back(runner: CliRunner) -> None:
    project, session, _ = _autonomous_study()
    _to_result_review(project, session)
    review = {
        "own_verdict": "inconclusive",
        "findings": "The conclusion overreaches.",
        "sources": ["model knowledge"],
    }
    reanalysis = resume_workflow(
        session, {**review, "decision": "reanalyze"}, project=project
    )
    assert _value(reanalysis)["step"] == "analyze"
    assert _value(reanalysis)["data"]["review_feedback"] == review["findings"]

    resume_workflow(
        session,
        {"verdict": "inconclusive", "conclusion": "run-1: loss 0.5, one run"},
        project=project,
    )
    implementing = resume_workflow(
        session,
        {**review, "decision": "fix_code", "findings": "The metric looks broken."},
        project=project,
    )
    assert _value(implementing)["step"] == "implement"
    experiment = project.vault.load("baseline")
    assert experiment.commit is None
    assert getattr(experiment, "verdict", None) is None


def test_code_review_must_not_change_the_commit(runner: CliRunner) -> None:
    project, session, _ = _autonomous_study()
    code, _ = _to_code_review(project, session)
    assert _value(code)["step"] == "review_code"
    experiment = project.vault.load("baseline")
    experiment.commit = _commit("changed during the review")
    project.vault.save(experiment, overwrite=True)

    rejected = resume_workflow(
        session, {"decision": "approve", "findings": "Fine."}, project=project
    )
    assert _value(rejected)["step"] == "review_code"
    assert "must not change code" in _value(rejected)["feedback"]


def test_reviews_are_skipped_when_a_person_approves(runner: CliRunner) -> None:
    project = Project.discover()
    project.vault.create("project-setup", type="context")
    Leaderboard.create(project, "runs", {"type": "csv", "path": "runs.csv"})
    task = project.vault.create(
        "task", type="task", metrics=[{"name": "loss", "direction": "minimize"}]
    )
    started = start_workflow(
        "research",
        {"task": task.id, "command": ["python", "train.py"]},
        project=project,
    )
    assert "wait for their approval" in _value(started)["instruction"]
    assert _value(started)["step"] == "design"
    implementing = _design(project, started["workflow_session_id"])
    assert _value(implementing)["step"] == "implement"
    assert _value(implementing)["data"]["smoke_test_required"] is False
