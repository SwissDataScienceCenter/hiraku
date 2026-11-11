from __future__ import annotations

import json
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anyio
import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.errors import ValidationError, WorkflowError
from hiraku.mcp import create_server
from hiraku.project import Project
from hiraku.workflows.langgraph import (
    resume_workflow,
    start_workflow,
    workflow_status,
)
from hiraku.workflows.session_history import (
    checkpoint_database,
    list_sessions,
    session_history,
)


def _write_workflow(project: Project, source: str) -> None:
    directory = project.workflows_dir / "broken"
    directory.mkdir()
    (directory / "workflow.py").write_text(source, encoding="utf-8")


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("def build_graph(checkpointer, *, project): pass\n", "input_schema()"),
        (
            "def input_schema(): return {'type': 'array'}\n"
            "def build_graph(checkpointer, *, project): pass\n",
            "JSON Schema object",
        ),
        (
            "def input_schema(): raise RuntimeError('schema failed')\n"
            "def build_graph(checkpointer, *, project): pass\n",
            "schema failed",
        ),
    ],
)
def test_start_rejects_invalid_workflow_input_schema(
    tmp_path: Path, source: str, message: str
) -> None:
    config = Project.create(tmp_path, install_skill=False)
    _write_workflow(config, source)

    with pytest.raises(WorkflowError, match=message):
        start_workflow("broken", project=config)


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        ("", "must export build_graph"),
        (
            "def build_graph(checkpointer, *, project):\n"
            "    raise RuntimeError('graph failed')\n",
            "Cannot build LangGraph workflow.*graph failed",
        ),
        (
            "def build_graph(checkpointer, *, project):\n    return object()\n",
            "must return a compiled graph",
        ),
    ],
)
def test_start_rejects_invalid_graph_factory(
    tmp_path: Path, factory: str, message: str
) -> None:
    config = Project.create(tmp_path, install_skill=False)
    _write_workflow(
        config,
        "def input_schema(): return {'type': 'object'}\n" + factory,
    )

    with pytest.raises(WorkflowError, match=message):
        start_workflow("broken", project=config)


def test_start_wraps_workflow_import_errors(tmp_path: Path) -> None:
    config = Project.create(tmp_path, install_skill=False)
    _write_workflow(config, "def input_schema():\n    return [\n")

    with pytest.raises(WorkflowError, match="Cannot import LangGraph workflow"):
        start_workflow("broken", project=config)


def test_workflow_status_rejects_unknown_session(tmp_path: Path) -> None:
    config = Project.create(tmp_path, example="workflows", install_skill=False)

    with pytest.raises(ValidationError, match="was not found"):
        workflow_status(f"unknown:{'0' * 32}", project=config)


def test_resume_rejects_stale_checkpoint(tmp_path: Path) -> None:
    config = Project.create(tmp_path, example="workflows", install_skill=False)
    started = start_workflow("count-steps", {"count": 1}, project=config)

    with pytest.raises(ValidationError, match="checkpoint has changed"):
        resume_workflow(
            started["workflow_session_id"],
            {"decision": "finish"},
            project=config,
            checkpoint_id="stale",
        )


def resume_current_workflow(session_id, response, *, project):
    status = workflow_status(session_id, project=project)
    return resume_workflow(
        session_id, response, project=project, checkpoint_id=status["checkpoint_id"]
    )


def _commit(path: Path) -> str:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.org"], cwd=path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
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


@pytest.mark.parametrize("task_id", ["task", "research/task"])
def test_collaborative_runs_a_bounded_experiment_with_langgraph(tmp_path, task_id):
    config = Project.create(tmp_path, install_skill=False)
    (config.leaderboards_dir / "default.yml").write_text(
        "source: {type: csv, path: runs.csv}\n"
    )
    config.set_active_record(config.vault.create(task_id, type="task", title="task"))
    commit = _commit(tmp_path)
    command = ["python", "train.py"]
    result = start_workflow(
        "collaborative", {"rounds": 1, "command": command}, project=config
    )
    assert _interrupt(result)["step"] == "draft"

    result = resume_current_workflow(
        result["workflow_session_id"],
        _approved(
            {
                "event": "drafted",
                "arguments": {
                    "name": "dropout",
                    "summary": "Add dropout",
                    "hypothesis": "Dropout improves validation accuracy",
                    "change": "Add dropout before the head",
                },
            }
        ),
        project=config,
    )
    assert _interrupt(result)["step"] == "implement"

    result = resume_current_workflow(
        result["workflow_session_id"],
        _approved({"event": "implemented", "arguments": {"commit": commit}}),
        project=config,
    )
    launch = _interrupt(result)
    assert launch["step"] == "launch"
    experiment_id = f"{task_id}/dropout"
    assert config.vault.load(experiment_id).parent_id == task_id
    assert (
        f"hiraku job run --record {experiment_id} -- python train.py"
        == (launch["data"]["command"])
    )

    with (tmp_path / "runs.csv").open("w", encoding="utf-8") as stream:
        stream.write(f"id,record_id,metric.val\nrun-1,{experiment_id},\n")
    result = resume_current_workflow(
        result["workflow_session_id"], _approved({"event": "launched"}), project=config
    )
    pending = _interrupt(result)
    assert pending["step"] == "analyze"
    assert pending["data"]["metrics_pending"] is True
    with (tmp_path / "runs.csv").open("w", encoding="utf-8") as stream:
        stream.write(f"id,record_id,metric.val\nrun-1,{experiment_id},0.8\n")
    result = resume_current_workflow(
        result["workflow_session_id"],
        _approved({"event": "revise_analysis", "arguments": {"feedback": "Retry"}}),
        project=config,
    )
    assert _interrupt(result)["step"] == "analyze"

    completed = resume_current_workflow(
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
        project=config,
    )
    assert completed["status"] == "completed"
    experiment = config.vault.load(experiment_id)
    assert experiment.state == "analyzed"
    assert experiment.verdict == "supports"
    assert "Summarize the run" in completed["result"]


def _project(tmp_path: Path) -> Project:
    return Project.create(tmp_path, example="workflows", install_skill=False)


def test_concurrent_resumes_reject_stale_checkpoint(tmp_path: Path) -> None:
    config = _project(tmp_path)
    started = start_workflow("count-steps", {"count": 2}, project=config)
    session_id = started["workflow_session_id"]
    token = started["interrupts"][0]["value"]["data"]["token"]

    def resume():
        try:
            return resume_workflow(
                session_id,
                {"token": token, "decision": "continue"},
                checkpoint_id=started["checkpoint_id"],
                project=config,
            )
        except ValidationError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: resume(), range(2)))
    assert sum(isinstance(result, ValidationError) for result in results) == 1
    assert sum(isinstance(result, dict) for result in results) == 1
    error = next(r for r in results if isinstance(r, Exception))
    assert "checkpoint has changed" in str(error)


def test_checkpoint_storage_is_ignored(tmp_path: Path) -> None:
    import subprocess

    config = _project(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    start_workflow("count-steps", project=config)
    database = checkpoint_database(config)
    assert database.is_file()
    assert (
        subprocess.run(
            ["git", "check-ignore", str(database)], cwd=tmp_path, check=False
        ).returncode
        == 0
    )


def test_session_history_survives_restart_and_missing_workflow(tmp_path: Path) -> None:
    config = _project(tmp_path)
    assert list_sessions(project=config) == []
    started = start_workflow(
        "count-steps",
        {"count": 1},
        project=config,
        context={"client": "codex", "session_id": "client-123"},
    )
    session_id = started["workflow_session_id"]
    token = started["interrupts"][0]["value"]["data"]["token"]
    completed = resume_workflow(
        session_id,
        {"token": token, "decision": "continue"},
        project=Project.load(config.path),
        checkpoint_id=started["checkpoint_id"],
        context={"client": "claude"},
    )
    assert completed["status"] == "completed"
    history = session_history(session_id, project=config)
    assert history["session"]["context"] == {
        "client": "codex",
        "session_id": "client-123",
    }
    assert history["session"]["status"] == "completed"
    assert [event["event"] for event in history["events"]] == [
        "started",
        "waiting",
        "resume_requested",
        "completed",
    ]
    assert history["events"][2]["context"] == {"client": "claude"}
    assert all("response" not in event for event in history["events"])
    assert list_sessions(project=config) == [history["session"]]

    with sqlite3.connect(checkpoint_database(config)) as connection:
        checkpoint = connection.execute(
            "SELECT 1 FROM checkpoints WHERE thread_id = ? AND checkpoint_id = ?",
            (session_id, history["session"]["checkpoint_id"]),
        ).fetchone()
    assert checkpoint is not None
    (config.workflows_dir / "count-steps" / "workflow.py").unlink()
    assert session_history(session_id, project=config) == history
    assert list_sessions(project=config) == [history["session"]]


def test_invalid_context_and_failed_start_are_recorded_safely(tmp_path: Path) -> None:
    config = _project(tmp_path)
    with pytest.raises(ValidationError, match="JSON values"):
        start_workflow("count-steps", project=config, context={"bad": object()})
    assert list_sessions(project=config) == []
    with pytest.raises(ValidationError, match="positive integer"):
        start_workflow("count-steps", {"count": 0}, project=config)
    session = list_sessions(project=config)[0]
    assert session["status"] == "failed"
    history = session_history(session["workflow_session_id"], project=config)
    assert [event["event"] for event in history["events"]] == ["started", "failed"]
    assert history["events"][-1]["error_type"] == "ValidationError"
    with pytest.raises(ValidationError, match="not found"):
        session_history("missing", project=config)


def test_resume_rebuilds_missing_session_index(tmp_path: Path) -> None:
    config = _project(tmp_path)
    started = start_workflow("count-steps", {"count": 1}, project=config)
    session_id = started["workflow_session_id"]
    token = started["interrupts"][0]["value"]["data"]["token"]
    with sqlite3.connect(checkpoint_database(config)) as connection:
        connection.execute("DROP TABLE hiraku_workflow_session_events")
        connection.execute("DROP TABLE hiraku_workflow_sessions")
    assert list_sessions(project=config) == []
    resume_workflow(
        session_id,
        {"token": token, "decision": "continue"},
        project=config,
        checkpoint_id=started["checkpoint_id"],
        context={"client": "resuming-client"},
    )
    history = session_history(session_id, project=config)
    assert history["session"]["context"] == {}
    assert [event["event"] for event in history["events"]] == [
        "resume_requested",
        "completed",
    ]
    assert history["events"][0]["context"] == {"client": "resuming-client"}


def test_cli_session_context_and_history(tmp_path: Path, monkeypatch) -> None:
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    context = tmp_path / "context.json"
    context.write_text('{"client": "cli"}', encoding="utf-8")
    runner = CliRunner()
    started = runner.invoke(
        main, ["workflow", "start", "count-steps", "--context-file", str(context)]
    )
    assert started.exit_code == 0, started.output
    session_id = json.loads(started.output)["workflow_session_id"]
    listed = runner.invoke(main, ["workflow", "sessions"])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.output)[0]["context"] == {"client": "cli"}
    history = runner.invoke(main, ["workflow", "history", session_id])
    assert history.exit_code == 0, history.output
    assert json.loads(history.output)["session"]["workflow_id"] == "count-steps"


def test_mcp_exposes_sessions_and_transition_context(tmp_path: Path) -> None:
    config = _project(tmp_path)
    server = create_server(config)
    started = anyio.run(
        server.call_tool,
        "workflow_start",
        {
            "workflow_id": "count-steps",
            "arguments": {"count": 1},
            "context": {"client_session": "abc"},
        },
    ).structured_content
    session_id = started["workflow_session_id"]
    token = started["interrupts"][0]["value"]["data"]["token"]
    anyio.run(
        server.call_tool,
        "workflow_resume",
        {
            "workflow_session_id": session_id,
            "response": {"token": token, "decision": "continue"},
            "checkpoint_id": started["checkpoint_id"],
            "context": {"trace": "xyz"},
        },
    )
    listed = anyio.run(server.call_tool, "workflow_sessions", {})
    assert listed.structured_content is not None
    history = anyio.run(
        server.call_tool,
        "workflow_session_history",
        {"workflow_session_id": session_id},
    ).structured_content
    assert history["session"]["context"] == {"client_session": "abc"}
    assert history["events"][2]["context"] == {"trace": "xyz"}
    assert history["session"]["status"] == "completed"
