from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anyio
import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.errors import ValidationError
from hiraku.mcp import create_server
from hiraku.project import Project
from hiraku.workflows.langgraph import resume_workflow, start_workflow
from hiraku.workflows.session_history import (
    checkpoint_database,
    list_sessions,
    session_history,
)


def _project(tmp_path: Path) -> Project:
    return Project.create(tmp_path, example="workflows", install_skill=False)


def test_concurrent_resumes_reject_stale_checkpoint(tmp_path: Path) -> None:
    config = _project(tmp_path)
    started = start_workflow("count-steps", {"count": 2}, config=config)
    session_id = started["workflow_session_id"]
    token = started["interrupts"][0]["value"]["data"]["token"]

    def resume():
        try:
            return resume_workflow(
                session_id,
                {"token": token, "decision": "continue"},
                checkpoint_id=started["checkpoint_id"],
                config=config,
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
    start_workflow("count-steps", config=config)
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
    assert list_sessions(config=config) == []
    started = start_workflow(
        "count-steps",
        {"count": 1},
        config=config,
        context={"client": "codex", "session_id": "client-123"},
    )
    session_id = started["workflow_session_id"]
    token = started["interrupts"][0]["value"]["data"]["token"]
    completed = resume_workflow(
        session_id,
        {"token": token, "decision": "continue"},
        config=Project.load(config.path),
        checkpoint_id=started["checkpoint_id"],
        context={"client": "claude"},
    )
    assert completed["status"] == "completed"
    history = session_history(session_id, config=config)
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
    assert list_sessions(config=config) == [history["session"]]

    with sqlite3.connect(checkpoint_database(config)) as connection:
        checkpoint = connection.execute(
            "SELECT 1 FROM checkpoints WHERE thread_id = ? AND checkpoint_id = ?",
            (session_id, history["session"]["checkpoint_id"]),
        ).fetchone()
    assert checkpoint is not None
    (config.workflows_dir / "count-steps" / "workflow.py").unlink()
    assert session_history(session_id, config=config) == history
    assert list_sessions(config=config) == [history["session"]]


def test_invalid_context_and_failed_start_are_recorded_safely(tmp_path: Path) -> None:
    config = _project(tmp_path)
    with pytest.raises(ValidationError, match="JSON values"):
        start_workflow("count-steps", config=config, context={"bad": object()})
    assert list_sessions(config=config) == []
    with pytest.raises(ValidationError, match="positive integer"):
        start_workflow("count-steps", {"count": 0}, config=config)
    session = list_sessions(config=config)[0]
    assert session["status"] == "failed"
    history = session_history(session["workflow_session_id"], config=config)
    assert [event["event"] for event in history["events"]] == ["started", "failed"]
    assert history["events"][-1]["error_type"] == "ValidationError"
    with pytest.raises(ValidationError, match="not found"):
        session_history("missing", config=config)


def test_legacy_checkpoint_is_indexed_when_resumed(tmp_path: Path) -> None:
    config = _project(tmp_path)
    started = start_workflow("count-steps", {"count": 1}, config=config)
    session_id = started["workflow_session_id"]
    token = started["interrupts"][0]["value"]["data"]["token"]
    with sqlite3.connect(checkpoint_database(config)) as connection:
        connection.execute("DROP TABLE hiraku_workflow_session_events")
        connection.execute("DROP TABLE hiraku_workflow_sessions")
    assert list_sessions(config=config) == []
    resume_workflow(
        session_id,
        {"token": token, "decision": "continue"},
        config=config,
        checkpoint_id=started["checkpoint_id"],
        context={"client": "resuming-client"},
    )
    history = session_history(session_id, config=config)
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
