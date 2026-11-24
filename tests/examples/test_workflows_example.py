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


def test_research_does_not_pick_an_existing_task_implicitly(
    runner: CliRunner,
) -> None:
    # Without a `task` argument research drafts one, even when a task exists.
    project = Project.discover()
    project.vault.create("project-setup", type="context")
    Leaderboard.create(project, "runs", {"type": "csv", "path": "runs.csv"})
    project.vault.create(
        "task", type="task", metrics=[{"name": "loss", "direction": "minimize"}]
    )
    started = start_workflow(
        "research", {"command": ["python", "train.py"]}, project=project
    )
    assert started["interrupts"][0]["value"]["step"] == "draft"


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


def _step(response: dict[str, Any]) -> str:
    return _value(response)["step"]


def _commit(message: str) -> str:
    subprocess.run(["git", "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", message], check=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


SOURCES = ["model knowledge"]
LOSS = {"run_id": "run-1", "metric": "loss", "value": 0.5}


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
            "reviews": ["design", "code", "result", "acceptance"],
            **options,
        },
        project=project,
    )
    return project, started["workflow_session_id"], started


def _plan(record_id: str = "baseline") -> dict[str, str]:
    return {
        "record_id": record_id,
        "summary": record_id.title(),
        "hypothesis": "Loss falls",
        "expected": "Lower loss",
        "change": "Train a model",
    }


def _design(project: Project, session: str, *names: str) -> dict[str, Any]:
    return resume_workflow(
        session,
        {"experiments": [_plan(name) for name in names or ("baseline",)]},
        project=project,
    )


def _finding(claim: str = "No control.", **fields: Any) -> dict[str, Any]:
    return {"claim": claim, "evidence": "See the plan.", **fields}


def _verdict(record_id: str, decision: str = "approve", *findings: dict[str, Any]):
    return {"record_id": record_id, "decision": decision, "findings": list(findings)}


def _design_review(
    project: Project, session: str, *verdicts: dict[str, Any]
) -> dict[str, Any]:
    return resume_workflow(
        session, {"verdicts": list(verdicts), "sources": SOURCES}, project=project
    )


def _approve_design(project: Project, session: str) -> dict[str, Any]:
    _design(project, session)
    return _design_review(project, session, _verdict("baseline"))


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


def _approve_code(project: Project, session: str) -> dict[str, Any]:
    return resume_workflow(session, {"decision": "approve"}, project=project)


def _write_runs(project: Project, *rows: str) -> None:
    (project.root / "runs.csv").write_text(
        "id,record_id,commit,metric.loss\n" + "".join(f"{row}\n" for row in rows)
    )


def _to_result_review(project: Project, session: str) -> str:
    response, sha = _to_code_review(project, session)
    assert _step(response) == "review_code"
    _approve_code(project, session)
    resume_workflow(session, {"event": "launched"}, project=project)
    _write_runs(project, f"run-1,baseline,{sha},0.5")
    resume_workflow(session, {"event": "checked"}, project=project)
    analyzed = resume_workflow(
        session,
        {"verdict": "supports", "conclusion": "run-1: loss 0.5"},
        project=project,
    )
    assert _step(analyzed) == "review_result"
    return sha


def _result_review(
    project: Project, session: str, decision: str = "approve", *findings: dict[str, Any]
) -> dict[str, Any]:
    return resume_workflow(
        session,
        {
            "own_verdict": "supports",
            "decision": decision,
            "findings": list(findings),
            "sources": SOURCES,
            "evidence": [LOSS],
        },
        project=project,
    )


def _answer(
    project: Project, session: str, id_: str, action: str, **fields: Any
) -> dict[str, Any]:
    return resume_workflow(
        session,
        {"responses": [{"id": id_, "action": action, "note": "Because."}], **fields},
        project=project,
    )


def _rule(
    project: Project, session: str, id_: str, ruling: str, *evidence: dict[str, Any]
) -> dict[str, Any]:
    return resume_workflow(
        session,
        {
            "rulings": [{"finding_id": id_, "ruling": ruling, "reason": "Weighed."}],
            "sources": SOURCES,
            "evidence": list(evidence),
        },
        project=project,
    )


def _dispute_until_meta(project: Project, session: str) -> dict[str, Any]:
    """Design review, a disputed answer, and a reviewer that keeps the finding."""
    _design(project, session)
    fixing = _design_review(project, session, _verdict("baseline", "fix", _finding()))
    assert _step(fixing) == "fix"
    again = _answer(project, session, "design-1.1", "dispute")
    assert _step(again) == "review_design"
    return _design_review(
        project,
        session,
        _verdict("baseline", "fix", _finding(continues="design-1.1")),
    )


def test_only_the_result_and_the_study_are_reviewed_by_default(
    runner: CliRunner,
) -> None:
    project, session, started = _autonomous_study(reviews=["result", "acceptance"])
    assert _step(started) == "design"
    # No design review: straight to implementation, and no review stage in the way.
    implementing = _design(project, session)
    assert _step(implementing) == "implement"
    launch, sha = _implement(project, session, "baseline")
    assert _step(launch) == "launch"
    assert project.vault.load("baseline").review_stages == ["result", "acceptance"]
    resume_workflow(session, {"event": "launched"}, project=project)
    _write_runs(project, f"run-1,baseline,{sha},0.5")
    resume_workflow(session, {"event": "checked"}, project=project)
    result = resume_workflow(
        session,
        {"verdict": "supports", "conclusion": "run-1: loss 0.5"},
        project=project,
    )
    assert _step(result) == "review_result"
    assert _step(_result_review(project, session)) == "triage"
    resume_workflow(session, {"decision": "stop", "reason": "Done"}, project=project)
    accepting = resume_workflow(session, {"report": "baseline: 0.5"}, project=project)
    assert _step(accepting) == "acceptance_review"
    experiment = project.vault.load("baseline")
    assert [item["kind"] for item in experiment.reviews] == ["result"]


def test_wait_names_runs_that_do_not_carry_the_experiment_commit(
    runner: CliRunner,
) -> None:
    # A tracking source that does not record the commit (for example a W&B script
    # without `hiraku.commit`): the runs finish but never match the experiment.
    project, session, _ = _autonomous_study(reviews=[])
    _design(project, session)
    _, sha = _implement(project, session, "baseline")
    waiting = resume_workflow(session, {"event": "launched"}, project=project)
    assert _step(waiting) == "wait"
    assert _value(waiting)["data"]["runs_left_out"] == {}
    _write_runs(project, "run-1,baseline,,0.5")
    stuck = resume_workflow(session, {"event": "checked"}, project=project)
    assert _step(stuck) == "wait"
    assert "run-1" in _value(stuck)["feedback"]
    assert "hiraku.commit" in _value(stuck)["feedback"]
    assert _value(stuck)["data"]["runs_left_out"] == {
        "baseline": [{"run_id": "run-1", "commit": None}]
    }
    # With the commit recorded the same experiment goes on to the analysis.
    _write_runs(project, f"run-1,baseline,{sha},0.5")
    analyzing = resume_workflow(session, {"event": "checked"}, project=project)
    assert _step(analyzing) == "analyze"


def test_wait_does_not_blame_runs_of_an_earlier_commit(runner: CliRunner) -> None:
    # A run of an earlier commit (for example before a review fix) has a commit;
    # it is ignored while the run of the current commit is pending, and the
    # agent is not told to send `failed`, which would spend a repair.
    project, session, _ = _autonomous_study(reviews=[])
    _design(project, session)
    _, sha = _implement(project, session, "baseline")
    resume_workflow(session, {"event": "launched"}, project=project)
    _write_runs(project, "run-old,baseline,deadbeef,0.4")
    pending = resume_workflow(session, {"event": "checked"}, project=project)
    assert _step(pending) == "wait"
    assert "run-old" in _value(pending)["feedback"]
    assert "earlier commit" in _value(pending)["feedback"]
    assert "send `failed`" not in _value(pending)["feedback"]
    assert _value(pending)["data"]["runs_left_out"] == {
        "baseline": [{"run_id": "run-old", "commit": "deadbeef"}]
    }
    _write_runs(project, "run-old,baseline,deadbeef,0.4", f"run-1,baseline,{sha},0.5")
    analyzing = resume_workflow(session, {"event": "checked"}, project=project)
    assert _step(analyzing) == "analyze"
    assert [item["run_id"] for item in _value(analyzing)["data"]["runs"]] == ["run-1"]


def test_reviews_option_defaults_and_validation(runner: CliRunner) -> None:
    from hiraku.resources.workflows.research.workflow import ResearchInput

    assert ResearchInput(command=["x"]).reviews == ["result", "acceptance"]
    with pytest.raises(ValueError):
        ResearchInput(command=["x"], reviews=["style"])


def test_an_autonomous_study_without_reviews_runs_straight_through(
    runner: CliRunner,
) -> None:
    project, session, _ = _autonomous_study(reviews=[])
    _design(project, session)
    _, sha = _implement(project, session, "baseline")
    resume_workflow(session, {"event": "launched"}, project=project)
    _write_runs(project, f"run-1,baseline,{sha},0.5")
    resume_workflow(session, {"event": "checked"}, project=project)
    triage = resume_workflow(
        session,
        {"verdict": "supports", "conclusion": "run-1: loss 0.5"},
        project=project,
    )
    assert _step(triage) == "triage"
    done = resume_workflow(
        session, {"decision": "stop", "reason": "Done"}, project=project
    )
    # No acceptance review, so no summary step either.
    assert done["status"] == "completed"


def test_the_study_ends_after_the_result_review_without_acceptance(
    runner: CliRunner,
) -> None:
    project, session, _ = _autonomous_study(reviews=["result"])
    _design(project, session)
    _, sha = _implement(project, session, "baseline")
    resume_workflow(session, {"event": "launched"}, project=project)
    _write_runs(project, f"run-1,baseline,{sha},0.5")
    resume_workflow(session, {"event": "checked"}, project=project)
    resume_workflow(
        session,
        {"verdict": "supports", "conclusion": "run-1: loss 0.5"},
        project=project,
    )
    assert _step(_result_review(project, session)) == "triage"
    done = resume_workflow(
        session, {"decision": "stop", "reason": "Done"}, project=project
    )
    assert done["status"] == "completed"


def test_autonomous_study_reviews_design_code_and_result(runner: CliRunner) -> None:
    project, session, started = _autonomous_study()
    assert _step(started) == "design"
    assert "do not wait for the user" in _value(started)["instruction"]

    reviewing = _design(project, session)
    assert _step(reviewing) == "review_design"
    assert "tracking.run.read" in _value(reviewing)["required_capabilities"]
    implementing = _design_review(project, session, _verdict("baseline"))
    assert _step(implementing) == "implement"
    assert _value(implementing)["data"]["smoke_test_required"] is True

    sha = _commit("baseline")
    missing = resume_workflow(session, {"commit": sha}, project=project)
    assert _step(missing) == "implement"
    assert "smoke test" in _value(missing)["feedback"]
    code = resume_workflow(
        session,
        {"commit": sha, "smoke_command": "python train.py", "smoke_output": "ok"},
        project=project,
    )
    assert _step(code) == "review_code"
    assert _value(code)["data"]["diff"].endswith(f"{sha}")
    assert _step(_approve_code(project, session)) == "launch"
    resume_workflow(session, {"event": "launched"}, project=project)
    _write_runs(project, f"run-1,baseline,{sha},0.5")
    resume_workflow(session, {"event": "checked"}, project=project)
    result = resume_workflow(
        session,
        {"verdict": "supports", "conclusion": "run-1: loss 0.5"},
        project=project,
    )
    assert _step(result) == "review_result"
    data = _value(result)["data"]
    assert data["runs"][0]["commit"] == sha
    assert {"job_id", "timestamp", "duration", "url", "raw"} <= set(data["runs"][0])
    assert _step(_result_review(project, session)) == "triage"
    summarizing = resume_workflow(
        session, {"decision": "stop", "reason": "Done"}, project=project
    )
    assert _step(summarizing) == "summarize"
    accepting = resume_workflow(session, {"report": "baseline: 0.5"}, project=project)
    assert _step(accepting) == "acceptance_review"
    completed = resume_workflow(
        session,
        {"outcome": "accept", "sources": SOURCES, "evidence": [LOSS]},
        project=project,
    )
    assert completed["status"] == "completed"
    experiment = project.vault.load("baseline")
    assert [item["kind"] for item in experiment.reviews] == [
        "design",
        "code",
        "result",
    ]
    task = project.vault.load("task")
    assert task.report == "baseline: 0.5"
    assert task.acceptance == {"outcome": "accept", "caveats": []}


def test_design_review_revises_in_place_and_keeps_approved_siblings(
    runner: CliRunner,
) -> None:
    project, session, _ = _autonomous_study()
    _design(project, session, "a", "b")
    fixing = _design_review(
        project,
        session,
        _verdict("a"),
        _verdict("b", "fix", _finding(affects="design")),
    )
    assert _step(fixing) == "fix"
    assert {"a", "b"} <= {record.id for record in project.vault.list()}
    assert project.vault.load("a").design_review == "approved"
    assert _value(fixing)["data"]["findings"][0]["id"] == "design-1.1"

    unchanged = _answer(project, session, "design-1.1", "done")
    assert _step(unchanged) == "fix"
    assert "nothing observable changed" in _value(unchanged)["feedback"]

    amended = {**_plan("b"), "change": "Train a model with a control"}
    reviewing = _answer(project, session, "design-1.1", "done", plan=amended)
    assert _step(reviewing) == "review_design"
    assert [item["id"] for item in _value(reviewing)["data"]["experiments"]] == ["b"]
    assert "control" in str(project.vault.load("b").markdown)
    # The rejected round stays on the record.
    assert [item["kind"] for item in project.vault.load("b").reviews] == [
        "design",
        "response",
    ]


def test_review_findings_need_a_blocking_one_to_fix(runner: CliRunner) -> None:
    project, session, _ = _autonomous_study()
    _design(project, session)
    bare = _design_review(project, session, _verdict("baseline", "fix"))
    assert _step(bare) == "review_design"
    assert "blocking finding" in _value(bare)["error"]
    minor = _design_review(
        project,
        session,
        _verdict("baseline", "approve", _finding("Name is vague.", severity="minor")),
    )
    assert _step(minor) == "implement"
    assert project.vault.load("baseline").limitations[0]["claim"] == "Name is vague."


def test_unfixable_design_finding_concludes_without_running(runner: CliRunner) -> None:
    project, session, _ = _autonomous_study()
    _design(project, session)
    summarizing = _design_review(
        project,
        session,
        _verdict("baseline", "fix", _finding("Untestable.", unfixable=True)),
    )
    # Nothing ran: no triage, and the study moves to its summary.
    assert _step(summarizing) == "summarize"
    experiment = project.vault.load("baseline")
    assert experiment.verdict == "inconclusive"
    assert experiment.conclusion.startswith("Not run: design did not pass review.")
    assert experiment.triage == "stop"
    assert experiment.review_failed["stage"] == "design"
    assert experiment.review_failed["findings"][0]["claim"] == "Untestable."


def test_meta_review_rules_when_author_and_reviewer_do_not_converge(
    runner: CliRunner,
) -> None:
    project, session, _ = _autonomous_study()
    meta = _dispute_until_meta(project, session)
    assert _step(meta) == "meta_review"
    data = _value(meta)["data"]
    assert data["open_findings"][0]["continues"] == "design-1.1"
    assert data["task"]["id"] == "task"
    assert data["tree"][0]["id"] == "baseline"
    assert "project_setup" in data

    wrong = _rule(project, session, "design-9.9", "overrule")
    assert _step(wrong) == "meta_review"
    implementing = _rule(project, session, "design-2.1", "overrule")
    assert _step(implementing) == "implement"
    experiment = project.vault.load("baseline")
    assert experiment.design_review == "approved"
    assert experiment.approved_by == {"design": "meta_review"}
    assert experiment.disputed is True
    assert [item["kind"] for item in experiment.reviews][-1] == "meta"


def test_meta_review_sustained_finding_concludes_the_experiment(
    runner: CliRunner,
) -> None:
    project, session, _ = _autonomous_study()
    _dispute_until_meta(project, session)
    summarizing = _rule(project, session, "design-2.1", "sustain")
    assert _step(summarizing) == "summarize"
    experiment = project.vault.load("baseline")
    assert experiment.conclusion.startswith("Not run: design did not pass review.")
    assert experiment.triage == "stop"


def test_exhausted_rounds_go_to_meta_review(runner: CliRunner) -> None:
    project, session, _ = _autonomous_study(review_rounds=1)
    _design(project, session)
    _design_review(project, session, _verdict("baseline", "fix", _finding()))
    amended = {**_plan(), "change": "Add a control"}
    _answer(project, session, "design-1.1", "done", plan=amended)
    meta = _design_review(project, session, _verdict("baseline", "fix", _finding()))
    assert _step(meta) == "meta_review"
    limited = _rule(project, session, "design-2.1", "limitation")
    assert _step(limited) == "implement"
    assert project.vault.load("baseline").limitations[0]["source"] == "meta_review"


def test_code_review_fix_returns_to_review_with_a_new_commit(
    runner: CliRunner,
) -> None:
    project, session, _ = _autonomous_study()
    code, _ = _to_code_review(project, session)
    assert _step(code) == "review_code"
    fixing = resume_workflow(
        session,
        {
            "decision": "fix",
            "findings": [_finding("Leaks evaluation data.", affects="code")],
        },
        project=project,
    )
    assert _step(fixing) == "fix"
    assert _value(fixing)["data"]["diff"].startswith("git diff")
    sha = _commit("baseline fixed")
    missing = _answer(project, session, "code-1.1", "done", commit=sha)
    assert "smoke test" in _value(missing)["feedback"]
    again = _answer(
        project,
        session,
        "code-1.1",
        "done",
        commit=sha,
        smoke_command="python train.py",
        smoke_output="ok",
    )
    assert _step(again) == "review_code"
    assert project.vault.load("baseline").commit == sha


def test_unfixable_result_finding_still_allows_a_follow_up(runner: CliRunner) -> None:
    project, session, _ = _autonomous_study()
    _to_result_review(project, session)
    triage = _result_review(
        project,
        session,
        "fix",
        _finding("One seed cannot settle it.", unfixable=True, affects="runs"),
    )
    assert _step(triage) == "triage"
    assert _value(triage)["data"]["can_follow_up"] is True
    assert _value(triage)["data"]["experiment"]["review_failed"]["stage"] == "result"
    experiment = project.vault.load("baseline")
    # The analysis stands as a provisional result, marked for a person to check.
    assert experiment.verdict == "supports"
    assert experiment.conclusion == "run-1: loss 0.5"
    assert experiment.result_review == "failed"
    assert experiment.review_failed == {
        "stage": "result",
        "reason": "No fix can resolve it",
        "findings": [{"id": "result-1.1", "claim": "One seed cannot settle it."}],
    }
    follow_up = resume_workflow(
        session, {"decision": "follow-up", "reason": "Add seeds"}, project=project
    )
    assert _step(follow_up) == "design"


def test_provisional_result_is_disclosed_to_summary_and_acceptance(
    runner: CliRunner,
) -> None:
    project, session, _ = _autonomous_study()
    _to_result_review(project, session)
    _result_review(
        project,
        session,
        "fix",
        _finding("One seed cannot settle it.", unfixable=True, affects="runs"),
    )
    summarizing = resume_workflow(
        session, {"decision": "stop", "reason": "Enough"}, project=project
    )
    assert _step(summarizing) == "summarize"
    data = _value(summarizing)["data"]
    assert data["provisional"] == [
        {
            "id": "baseline",
            "verdict": "supports",
            "stage": "result",
            "reason": "No fix can resolve it",
            "findings": [{"id": "result-1.1", "claim": "One seed cannot settle it."}],
        }
    ]
    assert data["best_results"][0]["provisional"] is True
    assert data["tree"][0]["review_failed"]["stage"] == "result"


def test_approval_after_a_reopen_clears_a_failed_review() -> None:
    import importlib.util

    import hiraku
    from hiraku.record import Record

    path = Path(hiraku.__file__).parent / "resources/workflows/research/workflow.py"
    spec = importlib.util.spec_from_file_location("research_workflow", path)
    assert spec and spec.loader
    workflow = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(workflow)
    experiment = Record(
        id="baseline",
        type="experiment",
        verdict="supports",
        conclusion="run-1: loss 0.5",
        review_failed={"stage": "result", "reason": "x", "findings": []},
    )
    workflow._approve(experiment, "result", "reviewer")
    assert experiment.review_failed is None
    assert experiment.result_review == "approved"


def test_result_review_cites_values_that_the_data_confirms(runner: CliRunner) -> None:
    project, session, _ = _autonomous_study()
    _to_result_review(project, session)
    wrong = resume_workflow(
        session,
        {
            "own_verdict": "supports",
            "decision": "approve",
            "sources": SOURCES,
            "evidence": [{**LOSS, "value": 0.1}],
        },
        project=project,
    )
    assert _step(wrong) == "review_result"
    assert "not 0.1" in _value(wrong)["feedback"]
    none = resume_workflow(
        session,
        {"own_verdict": "supports", "decision": "approve", "sources": SOURCES},
        project=project,
    )
    assert "Cite the run values" in _value(none)["feedback"]
    assert _step(_result_review(project, session)) == "triage"


def test_result_fix_routes_on_what_changed(runner: CliRunner) -> None:
    project, session, _ = _autonomous_study(review_rounds=3)
    sha = _to_result_review(project, session)
    finding = _finding("The conclusion overreaches.", affects="analysis")
    _result_review(project, session, "fix", finding)

    # A revised conclusion goes back to the result review.
    revised = _answer(
        project,
        session,
        "result-1.1",
        "done",
        verdict="inconclusive",
        conclusion="run-1: loss 0.5, one run",
    )
    assert _step(revised) == "review_result"
    assert _value(revised)["data"]["analysis"]["verdict"] == "inconclusive"

    # A new run for the same commit restarts the analysis.
    _result_review(project, session, "fix", {**finding, "affects": "runs"})
    _write_runs(project, f"run-1,baseline,{sha},0.5", f"run-2,baseline,{sha},0.6")
    analyzing = _answer(project, session, "result-2.1", "done")
    assert _step(analyzing) == "analyze"
    assert len(_value(analyzing)["data"]["runs"]) == 2

    # A run that is declared invalid is dropped, with its reason kept.
    reviewing = resume_workflow(
        session, {"verdict": "supports", "conclusion": "two runs"}, project=project
    )
    assert _step(reviewing) == "review_result"
    resume_workflow(
        session,
        {
            "own_verdict": "supports",
            "decision": "fix",
            "findings": [_finding("run-2 diverged.", affects="runs")],
            "sources": SOURCES,
            "evidence": [{"run_id": "run-2", "metric": "loss", "value": 0.6}],
        },
        project=project,
    )
    analyzing = _answer(
        project,
        session,
        "result-3.1",
        "done",
        excluded_runs=[{"run_id": "run-2", "reason": "Diverged at step 3"}],
    )
    assert _step(analyzing) == "analyze"
    assert [item["run_id"] for item in _value(analyzing)["data"]["runs"]] == ["run-1"]
    resume_workflow(
        session, {"verdict": "supports", "conclusion": "run-1 only"}, project=project
    )
    visible = _result_review(project, session)
    assert _step(visible) == "triage"


def test_excluded_runs_stay_visible_to_the_result_review(runner: CliRunner) -> None:
    project, session, _ = _autonomous_study()
    sha = _to_result_review(project, session)
    _write_runs(project, f"run-1,baseline,{sha},0.5", "run-old,baseline,deadbeef,0.1")
    resume_workflow(
        session,
        {
            "own_verdict": "supports",
            "decision": "fix",
            "findings": [_finding("Check the other run.", affects="runs")],
            "sources": SOURCES,
            "evidence": [LOSS],
        },
        project=project,
    )
    reviewing = _answer(project, session, "result-1.1", "dispute")
    data = _value(reviewing)["data"]
    assert [item["run_id"] for item in data["excluded_runs"]] == ["run-old"]
    assert data["excluded_runs"][0]["reason"].startswith("commit differs")


def test_disputed_result_finding_that_stays_open_goes_to_meta_review(
    runner: CliRunner,
) -> None:
    project, session, _ = _autonomous_study()
    _to_result_review(project, session)
    finding = _finding("The metric looks broken.", affects="code")
    _result_review(project, session, "fix", finding)
    again = _answer(project, session, "result-1.1", "dispute")
    assert _step(again) == "review_result"
    meta = _result_review(
        project, session, "fix", {**finding, "continues": "result-1.1"}
    )
    assert _step(meta) == "meta_review"
    assert _value(meta)["data"]["runs"][0]["run_id"] == "run-1"
    triage = _rule(project, session, "result-2.1", "sustain", LOSS)
    assert _step(triage) == "triage"
    experiment = project.vault.load("baseline")
    assert experiment.verdict == "supports"
    assert experiment.review_failed["reason"] == "The meta review sustained"
    assert experiment.disputed is True


def test_frozen_fields_and_scope_guard_during_a_fix(runner: CliRunner) -> None:
    project, session, _ = _autonomous_study()
    _to_result_review(project, session)
    _result_review(project, session, "fix", _finding("Weak.", affects="analysis"))

    experiment = project.vault.load("baseline")
    experiment.hypothesis = "Loss falls faster"
    project.vault.save(experiment, overwrite=True)
    frozen = _answer(project, session, "result-1.1", "dispute")
    assert _step(frozen) == "fix"
    assert "frozen" in _value(frozen)["feedback"]
    assert project.vault.load("baseline").hypothesis == "Loss falls"

    task = project.vault.load("task")
    task.metrics = [{"name": "loss", "direction": "maximize"}]
    project.vault.save(task, overwrite=True)
    asked = _answer(project, session, "result-1.1", "dispute")
    assert _step(asked) == "researcher"
    assert _value(asked)["data"]["question"]["records"] == ["task"]
    assert _value(asked)["data"]["question"]["reason"] == "scope"


def test_out_of_scope_finding_waits_for_the_researcher_alone(
    runner: CliRunner,
) -> None:
    project, session, _ = _autonomous_study()
    _design(project, session, "a", "b")
    implementing = _design_review(
        project,
        session,
        _verdict("a", "fix", _finding("The metric is wrong.", affects="task")),
        _verdict("b"),
    )
    # `b` is not held back by `a`'s open question.
    assert _step(implementing) == "implement"
    assert _value(implementing)["data"]["experiment"]["record_id"] == "b"
    sha = _commit("b")
    asked = resume_workflow(
        session,
        {"commit": sha, "smoke_command": "x", "smoke_output": "y"},
        project=project,
    )
    assert _step(asked) == "review_code"
    resume_workflow(session, {"decision": "approve"}, project=project)
    resume_workflow(session, {"event": "launched"}, project=project)
    _write_runs(project, f"run-b,b,{sha},0.5")
    resume_workflow(session, {"event": "checked"}, project=project)
    resume_workflow(
        session, {"verdict": "supports", "conclusion": "run-b: 0.5"}, project=project
    )
    resume_workflow(
        session,
        {
            "own_verdict": "supports",
            "decision": "approve",
            "sources": SOURCES,
            "evidence": [{"run_id": "run-b", "metric": "loss", "value": 0.5}],
        },
        project=project,
    )
    ask = resume_workflow(
        session, {"decision": "stop", "reason": "Done"}, project=project
    )
    assert _step(ask) == "researcher"
    assert "wait for their answer" in _value(ask)["instruction"]
    assert _value(ask)["data"]["experiment"]["record_id"] == "a"

    review = resume_workflow(
        session, {"answer": "resolved", "note": "Metric fixed"}, project=project
    )
    assert _step(review) == "review_design"
    assert [item["id"] for item in _value(review)["data"]["experiments"]] == ["a"]


@pytest.mark.parametrize(
    ("answer", "after"), [("accept_risk", "implement"), ("conclude", "summarize")]
)
def test_researcher_answers_accept_the_risk_or_conclude(
    runner: CliRunner, answer: str, after: str
) -> None:
    project, session, _ = _autonomous_study()
    _design(project, session)
    asked = _design_review(
        project,
        session,
        _verdict("baseline", "fix", _finding("Wrong metric.", affects="setup")),
    )
    assert _step(asked) == "researcher"
    response = resume_workflow(session, {"answer": answer}, project=project)
    assert _step(response) == after
    experiment = project.vault.load("baseline")
    if answer == "accept_risk":
        assert experiment.approved_by == {"design": "researcher"}
        assert experiment.limitations[0]["source"] == "researcher"
    else:
        assert experiment.triage == "stop"
        assert experiment.review_failed["stage"] == "design"


def test_one_agent_cannot_act_in_two_roles(runner: CliRunner) -> None:
    project, session, _ = _autonomous_study()
    resume_workflow(
        session,
        {"experiments": [_plan()], "agent": "session-1"},
        project=project,
    )
    clash = resume_workflow(
        session,
        {"verdicts": [_verdict("baseline")], "sources": SOURCES, "agent": "session-1"},
        project=project,
    )
    assert _step(clash) == "review_design"
    assert "already acted as author" in _value(clash)["error"]
    implementing = resume_workflow(
        session,
        {"verdicts": [_verdict("baseline")], "sources": SOURCES, "agent": "session-2"},
        project=project,
    )
    assert _step(implementing) == "implement"


def test_acceptance_review_reopens_an_experiment_once(runner: CliRunner) -> None:
    project, session, _ = _autonomous_study()
    _to_result_review(project, session)
    _result_review(project, session)
    resume_workflow(session, {"decision": "stop", "reason": "Done"}, project=project)
    resume_workflow(session, {"report": "baseline wins"}, project=project)
    reopen = {
        "outcome": "reopen",
        "sources": SOURCES,
        "evidence": [LOSS],
        "reopen": [
            {
                "record_id": "baseline",
                "findings": [_finding("One seed.", affects="runs")],
            }
        ],
    }
    fixing = resume_workflow(session, reopen, project=project)
    assert _step(fixing) == "fix"
    assert _value(fixing)["data"]["kind"] == "acceptance"
    assert project.vault.load("task").report is None

    reviewing = _answer(
        project,
        session,
        "acceptance-1.1",
        "done",
        verdict="inconclusive",
        conclusion="One seed only",
    )
    assert _step(reviewing) == "review_result"
    resume_workflow(
        session,
        {
            "own_verdict": "inconclusive",
            "decision": "approve",
            "sources": SOURCES,
            "evidence": [LOSS],
        },
        project=project,
    )
    resume_workflow(session, {"report": "baseline: one seed"}, project=project)
    # The reopen budget is spent: the outcome records what stays open.
    done = resume_workflow(session, reopen, project=project)
    assert done["status"] == "completed"
    acceptance = project.vault.load("task").acceptance
    assert acceptance["outcome"] == "not_accepted"
    assert acceptance["caveats"] == ["baseline: One seed."]


def test_code_review_must_not_change_the_commit(runner: CliRunner) -> None:
    project, session, _ = _autonomous_study()
    code, _ = _to_code_review(project, session)
    assert _step(code) == "review_code"
    experiment = project.vault.load("baseline")
    experiment.commit = _commit("changed during the review")
    project.vault.save(experiment, overwrite=True)

    rejected = _approve_code(project, session)
    assert _step(rejected) == "review_code"
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
