from __future__ import annotations

import subprocess
from pathlib import Path

from hiraku.config import HirakuConfig
from hiraku.record import Record, open_workspace
from hiraku.workflows.langgraph import resume_workflow as _resume_workflow
from hiraku.workflows.langgraph import start_workflow, workflow_status


def resume_workflow(session_id, response, *, config):
    status = workflow_status(session_id, config=config)
    return _resume_workflow(
        session_id, response, config=config, checkpoint_id=status["checkpoint_id"]
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


def _approved(response: dict) -> dict:
    return {"approval": {"decision": "approve"}, "response": response}


def test_collaborative_runs_a_bounded_experiment_with_langgraph(tmp_path) -> None:
    config = HirakuConfig.init(
        tmp_path, install_skill=False, records_path="hiraku/records"
    )
    workspace = open_workspace(config)
    _created_record = Record.create(workspace, "task/task.md", id="task", type="task")
    config.set_active_record(_created_record)
    commit = _commit(tmp_path)
    command = ["python", "train.py"]
    result = start_workflow(
        "collaborative", {"rounds": 1, "command": command}, config=config
    )
    assert _interrupt(result)["step"] == "draft"

    result = resume_workflow(
        result["workflow_session_id"],
        _approved(
            {
                "event": "drafted",
                "arguments": {
                    "record_id": "dropout",
                    "summary": "Add dropout",
                    "hypothesis": "Dropout improves validation accuracy",
                    "change": "Add dropout before the head",
                },
            }
        ),
        config=config,
    )
    assert _interrupt(result)["step"] == "implement"

    result = resume_workflow(
        result["workflow_session_id"],
        _approved({"event": "implemented", "arguments": {"commit": commit}}),
        config=config,
    )
    launch = _interrupt(result)
    assert launch["step"] == "launch"
    assert "hiraku run --record dropout -- python train.py" in launch["data"]["command"]

    with (tmp_path / "runs.csv").open("w", encoding="utf-8") as stream:
        stream.write("id,record_id,entrypoint,metric.val\nrun-1,dropout,train,\n")
    result = resume_workflow(
        result["workflow_session_id"], _approved({"event": "launched"}), config=config
    )
    pending = _interrupt(result)
    assert pending["step"] == "analyze"
    assert pending["data"]["metrics_pending"] is True
    with (tmp_path / "runs.csv").open("w", encoding="utf-8") as stream:
        stream.write("id,record_id,entrypoint,metric.val\nrun-1,dropout,train,0.8\n")
    result = resume_workflow(
        result["workflow_session_id"],
        _approved({"event": "revise_analysis", "arguments": {"feedback": "Retry"}}),
        config=config,
    )
    assert _interrupt(result)["step"] == "analyze"

    completed = resume_workflow(
        result["workflow_session_id"],
        _approved(
            {
                "event": "analyzed",
                "arguments": {
                    "verdict": "supports",
                    "conclusion": "Accuracy improved.",
                },
            }
        ),
        config=config,
    )
    assert completed["status"] == "completed"
    experiment = Record.load(open_workspace(config), "dropout")
    assert experiment.status == "analyzed"
    assert experiment.verdict == "supports"
    assert "Summarize the run" in completed["result"]
