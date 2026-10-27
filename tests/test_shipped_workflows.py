from __future__ import annotations

import subprocess
from pathlib import Path

from hiraku.config import HirakuConfig
from hiraku.record import Record, load_record
from hiraku.workflows.langgraph import resume_workflow, start_workflow


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
    config = HirakuConfig.init(tmp_path, install_skill=False)
    Record.create(
        "task",
        type="task",
        config=config,
    )
    commit = _commit(tmp_path)
    command = ["python", "train.py"]
    result = start_workflow(
        "collaborative", {"rounds": 1, "command": command}, config=config
    )
    assert _interrupt(result)["step"] == "draft"

    result = resume_workflow(
        result["run_id"],
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
        result["run_id"],
        _approved({"event": "implemented", "arguments": {"commit": commit}}),
        config=config,
    )
    launch = _interrupt(result)
    assert launch["step"] == "launch"
    assert "hiraku run --record dropout -- python train.py" in launch["data"]["command"]

    with (tmp_path / "runs.csv").open("w", encoding="utf-8") as stream:
        stream.write("id,record_id,entrypoint,metric.val\nrun-1,dropout,train,0.8\n")
    result = resume_workflow(
        result["run_id"], _approved({"event": "launched"}), config=config
    )
    assert _interrupt(result)["step"] == "analyze"

    completed = resume_workflow(
        result["run_id"],
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
    experiment = load_record("dropout", config=config)
    assert experiment.status == "analyzed"
    assert experiment.verdict == "supports"
    assert "Summarize the run" in completed["result"]
