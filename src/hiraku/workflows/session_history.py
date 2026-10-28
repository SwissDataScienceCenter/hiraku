"""Hiraku session history stored alongside LangGraph checkpoints."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import HirakuConfig
from ..errors import ValidationError

CHECKPOINTS = "langgraph-checkpoints.sqlite"


def checkpoint_database(config: HirakuConfig) -> Path:
    """Prepare a local-only home for checkpoints and session locks."""
    directory = config.hiraku_dir / ".hiraku"
    directory.mkdir(parents=True, exist_ok=True)
    ignore = directory / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")
    # Preserve access to databases created by earlier versions.
    legacy = config.hiraku_dir / CHECKPOINTS
    return legacy if legacy.exists() else directory / CHECKPOINTS


def _context_json(context: Mapping[str, Any] | None) -> str:
    if context is not None and not isinstance(context, Mapping):
        raise ValidationError("Session context must be a JSON object")
    try:
        return json.dumps(dict(context or {}), allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Session context must contain JSON values") from exc


def validate_context(context: Mapping[str, Any] | None) -> None:
    """Validate context before a workflow is started or resumed."""
    _context_json(context)


def _initialize(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS hiraku_workflow_sessions (
            workflow_session_id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            context TEXT NOT NULL,
            checkpoint_id TEXT
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS hiraku_workflow_session_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_session_id TEXT NOT NULL,
            event TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            context TEXT NOT NULL,
            checkpoint_id TEXT,
            error_type TEXT,
            FOREIGN KEY (workflow_session_id)
                REFERENCES hiraku_workflow_sessions(workflow_session_id)
        )
    """)
    connection.execute("""
        CREATE INDEX IF NOT EXISTS hiraku_workflow_session_events_session
        ON hiraku_workflow_session_events(workflow_session_id, id)
    """)


def record_event(
    workflow_session_id: str,
    workflow_id: str,
    event: str,
    status: str,
    *,
    config: HirakuConfig,
    context: Mapping[str, Any] | None = None,
    checkpoint_id: str | None = None,
    error_type: str | None = None,
) -> None:
    """Atomically append an event and update Hiraku's session index."""
    encoded = _context_json(context)
    timestamp = datetime.now(UTC).isoformat()
    database = checkpoint_database(config)
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database, timeout=30)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        _initialize(connection)
        connection.execute(
            """INSERT INTO hiraku_workflow_sessions
               (workflow_session_id, workflow_id, status, created_at,
                updated_at, context, checkpoint_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(workflow_session_id) DO UPDATE SET
                 status=excluded.status, updated_at=excluded.updated_at,
                 checkpoint_id=COALESCE(excluded.checkpoint_id, checkpoint_id)
            """,
            (
                workflow_session_id,
                workflow_id,
                status,
                timestamp,
                timestamp,
                encoded if event == "started" else "{}",
                checkpoint_id,
            ),
        )
        connection.execute(
            """INSERT INTO hiraku_workflow_session_events
               (workflow_session_id, event, timestamp, context, checkpoint_id,
                error_type) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                workflow_session_id,
                event,
                timestamp,
                encoded,
                checkpoint_id,
                error_type,
            ),
        )


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["context"] = json.loads(result["context"])
    return result


def list_sessions(*, config: HirakuConfig) -> list[dict[str, Any]]:
    """List indexed sessions without loading workflow code."""
    database = checkpoint_database(config)
    if not database.is_file():
        return []
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        if not _has_tables(connection):
            return []
        return [
            _decode(row)
            for row in connection.execute(
                "SELECT * FROM hiraku_workflow_sessions "
                "ORDER BY created_at DESC, workflow_session_id DESC"
            )
        ]


def session_history(
    workflow_session_id: str, *, config: HirakuConfig
) -> dict[str, Any]:
    """Read stored session metadata and events without executing the graph."""
    database = checkpoint_database(config)
    if database.is_file():
        with closing(sqlite3.connect(database)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN")
            if _has_tables(connection):
                session = connection.execute(
                    "SELECT * FROM hiraku_workflow_sessions "
                    "WHERE workflow_session_id = ?",
                    (workflow_session_id,),
                ).fetchone()
                if session is not None:
                    events = connection.execute(
                        "SELECT id, event, timestamp, context, checkpoint_id, "
                        "error_type FROM hiraku_workflow_session_events "
                        "WHERE workflow_session_id = ? ORDER BY id",
                        (workflow_session_id,),
                    )
                    return {
                        "session": _decode(session),
                        "events": [_decode(row) for row in events],
                    }
    raise ValidationError(f"Workflow session {workflow_session_id!r} was not found")


def _has_tables(connection: sqlite3.Connection) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='hiraku_workflow_sessions'"
        ).fetchone()
        is not None
    )
