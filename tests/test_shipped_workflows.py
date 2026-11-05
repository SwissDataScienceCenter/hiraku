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
    assert _interrupt(result)["step"] == "draft"

    result = resume_workflow(
        result["workflow_session_id"],
        {
            "event": "drafted",
            "arguments": {
                "record_id": "dropout",
                "summary": "Add dropout",
                "hypothesis": "Dropout improves validation accuracy",
                "change": "Add dropout before the head",
                "command": ["python", "train.py", "--dropout", "0.2"],
            },
        },
        project=config,
    )
    draft_gate = _interrupt(result)
    assert draft_gate["actor"] == "human"
    result = resume_workflow(
        result["workflow_session_id"],
        {
            "approval": {
                "decision": "approve",
                "revision": draft_gate["data"]["proposal_revision"],
            },
        },
        project=config,
    )
    assert _interrupt(result)["step"] == "implement"
    assert load_record("dropout", project=config).command == [
        "python",
        "train.py",
        "--dropout",
        "0.2",
    ]

    result = resume_workflow(
        result["workflow_session_id"],
        {
            "event": "implementation_plan",
            "arguments": {"plan": "Add dropout and commit the change."},
        },
        project=config,
    )
    plan_gate = _interrupt(result)
    assert plan_gate["interaction"]["type"] == "approval"
    result = resume_workflow(
        result["workflow_session_id"],
        {
            "approval": {
                "decision": "approve",
                "revision": plan_gate["data"]["plan_revision"],
            },
            "response": {
                "event": "implementation_approved",
                "arguments": {"revision": plan_gate["data"]["plan_revision"]},
            },
        },
        project=config,
    )
    execution = _interrupt(result)
    assert execution["step"] == "implement"
    assert execution["phase"] == "execute"

    result = resume_workflow(
        result["workflow_session_id"],
        {"event": "implemented", "arguments": {"commit": commit}},
        project=config,
    )
    launch = _interrupt(result)
    assert launch["step"] == "launch"
    assert launch["data"]["command"] == (
        "hiraku run --record dropout -- python train.py --dropout 0.2"
    )

    result = resume_workflow(
        result["workflow_session_id"],
        {
            "event": "launch_plan",
            "arguments": {"command": launch["data"]["command"]},
        },
        project=config,
    )
    launch_gate = _interrupt(result)
    result = resume_workflow(
        result["workflow_session_id"],
        {
            "approval": {
                "decision": "approve",
                "revision": launch_gate["data"]["plan_revision"],
            },
            "response": {
                "event": "launch_approved",
                "arguments": {"revision": launch_gate["data"]["plan_revision"]},
            },
        },
        project=config,
    )
    execution = _interrupt(result)
    assert execution["step"] == "launch"
    assert execution["phase"] == "execute"

    with (tmp_path / "runs.csv").open("w", encoding="utf-8") as stream:
        stream.write(
            "id,record_id,commit,entrypoint,metric.val\n"
            f"run-1,dropout,{commit},python train.py,\n"
        )
    result = resume_workflow(
        result["workflow_session_id"],
        {"event": "launched", "arguments": {"run_id": "run-1"}},
        project=config,
    )
    pending = _interrupt(result)
    assert pending["step"] == "analyze"
    assert pending["data"]["metrics_pending"] is True
    with (tmp_path / "runs.csv").open("w", encoding="utf-8") as stream:
        stream.write(
            "id,record_id,commit,entrypoint,metric.val\n"
            f"run-1,dropout,{commit},python train.py,0.8\n"
        )
    result = resume_workflow(
        result["workflow_session_id"],
        {"event": "revise_analysis", "arguments": {"feedback": "Retry"}},
        project=config,
    )
    assert _interrupt(result)["step"] == "analyze"

    completed = resume_workflow(
        result["workflow_session_id"],
        {
            "event": "analyzed",
            "arguments": {
                "verdict": "supports",
                "conclusion": "Accuracy improved.",
            },
        },
        project=config,
    )
    results_gate = _interrupt(completed)
    assert results_gate["actor"] == "human"
    completed = resume_workflow(
        completed["workflow_session_id"],
        {"approval": {"decision": "approve"}},
        project=config,
    )
    assert completed["status"] == "completed"
    experiment = load_record("dropout", project=config)
    assert experiment.status == "analyzed"
    assert experiment.verdict == "supports"
    assert "remaining limitations" in completed["result"]
