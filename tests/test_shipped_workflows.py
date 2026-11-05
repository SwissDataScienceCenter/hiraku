from __future__ import annotations

import subprocess
from pathlib import Path

from hiraku.project import Project
from hiraku.record import Record, load_record
from hiraku.workflows.langgraph import resume_workflow as _resume_workflow
from hiraku.workflows.langgraph import start_workflow, workflow_status


def resume_workflow(session_id, response, *, project):
    status = workflow_status(session_id, project=project)
    return _resume_workflow(
        session_id, response, project=project, checkpoint_id=status["checkpoint_id"]
    )


def _commit(path: Path) -> str:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.org"], cwd=path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=path, check=True)
    (path / "train.py").write_text("# experiment\n", encoding="utf-8")
    subprocess.run(["git", "add", "train.py"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "experiment"], cwd=path, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _interrupt(result: dict) -> dict:
    assert result["status"] == "waiting"
    assert len(result["interrupts"]) == 1
    return result["interrupts"][0]["value"]


def test_collaborative_runs_a_bounded_experiment_with_langgraph(tmp_path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    Record.create(
        "task",
        type="task",
        project=config,
    )
    commit = _commit(tmp_path)
    command = ["python", "train.py"]
    result = start_workflow(
        "collaborative", {"rounds": 1, "command": command}, project=config
    )
    assert _interrupt(result)["action"] == "draft_experiment"

    result = resume_workflow(
        result["workflow_session_id"],
        {
            "record_id": "dropout",
            "summary": "Add dropout",
            "hypothesis": "Dropout improves validation accuracy",
            "change": "Add dropout before the head",
            "command": ["python", "train.py", "--dropout", "0.2"],
        },
        project=config,
    )
    draft_gate = _interrupt(result)
    assert draft_gate["actor"] == "human"
    assert draft_gate["interaction"] == "approval"
    result = resume_workflow(
        result["workflow_session_id"],
        {
            "decision": "approve",
            "revision": draft_gate["revision"],
        },
        project=config,
    )
    assert _interrupt(result)["action"] == "prepare_implementation_plan"
    assert load_record("dropout", project=config).command == [
        "python",
        "train.py",
        "--dropout",
        "0.2",
    ]

    result = resume_workflow(
        result["workflow_session_id"],
        {"plan": "Add dropout and commit the change."},
        project=config,
    )
    plan_gate = _interrupt(result)
    assert plan_gate["interaction"] == "approval"
    result = resume_workflow(
        result["workflow_session_id"],
        {
            "decision": "approve",
            "revision": plan_gate["revision"],
        },
        project=config,
    )
    assert _interrupt(result)["action"] == "implement_experiment"

    result = resume_workflow(
        result["workflow_session_id"],
        {"commit": commit},
        project=config,
    )
    assert _interrupt(result)["action"] == "prepare_launch"

    result = resume_workflow(
        result["workflow_session_id"],
        {"command": "hiraku run --record dropout -- python train.py --dropout 0.2"},
        project=config,
    )
    launch_gate = _interrupt(result)
    assert launch_gate["interaction"] == "approval"
    result = resume_workflow(
        result["workflow_session_id"],
        {
            "decision": "approve",
            "revision": launch_gate["revision"],
        },
        project=config,
    )
    assert _interrupt(result)["action"] == "launch_experiment"

    with (tmp_path / "runs.csv").open("w", encoding="utf-8") as stream:
        stream.write(
            "id,record_id,commit,entrypoint,metric.val\n"
            f"run-1,dropout,{commit},python train.py,0.8\n"
        )
    result = resume_workflow(
        result["workflow_session_id"],
        {"run_id": "run-1"},
        project=config,
    )
    pending = _interrupt(result)
    assert pending["action"] == "analyze_results"
    assert pending["data"]["runs"][0]["metrics"]["val"] == "0.8"
    completed = resume_workflow(
        result["workflow_session_id"],
        {
            "verdict": "supports",
            "conclusion": "Accuracy improved.",
        },
        project=config,
    )
    results_gate = _interrupt(completed)
    assert results_gate["actor"] == "human"
    completed = resume_workflow(
        completed["workflow_session_id"],
        {"decision": "approve"},
        project=config,
    )
    assert completed["status"] == "completed"
    experiment = load_record("dropout", project=config)
    assert experiment.status == "analyzed"
    assert experiment.verdict == "supports"
    assert "remaining limitations" in completed["result"]


def test_collaborative_context_reloads_for_checkpoint_resume(tmp_path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    Record.create("task", type="task", project=config)
    started = start_workflow(
        "collaborative",
        {"rounds": 1, "command": ["python", "train.py"]},
        project=config,
    )
    initial = _interrupt(started)
    assert initial["actor"] == "agent"
    assert "response_schema" not in initial

    restarted_config = Project.discover(tmp_path)
    restored = workflow_status(started["workflow_session_id"], project=restarted_config)
    assert restored["checkpoint_id"] == started["checkpoint_id"]
    assert _interrupt(restored)["action"] == "draft_experiment"

    drafted = resume_workflow(
        started["workflow_session_id"],
        {
            "record_id": "restart-draft",
            "summary": "Draft after restart",
            "hypothesis": "The change improves the metric",
            "change": "Change one setting",
            "command": ["python", "train.py", "--setting", "1"],
        },
        project=restarted_config,
    )
    approval = _interrupt(drafted)
    assert approval["interaction"] == "approval"
    resumed = resume_workflow(
        started["workflow_session_id"],
        {"decision": "approve", "revision": approval["revision"]},
        project=restarted_config,
    )
    assert _interrupt(resumed)["action"] == "prepare_implementation_plan"
