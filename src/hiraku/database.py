from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import HirakuError

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS goals (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    objective TEXT NOT NULL DEFAULT '',
    metric TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('min', 'max')),
    target REAL,
    evaluation_protocol TEXT NOT NULL DEFAULT '',
    constraints_json TEXT NOT NULL DEFAULT '[]',
    baseline_commit TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'closed')),
    launch_manifest_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiments (
    id TEXT NOT NULL,
    goal_id TEXT NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    parent_id TEXT,
    kind TEXT NOT NULL CHECK(kind IN ('baseline', 'hypothesis')),
    hypothesis TEXT NOT NULL,
    branch TEXT NOT NULL UNIQUE,
    parent_commit TEXT NOT NULL,
    evaluated_commit TEXT,
    status TEXT NOT NULL CHECK(status IN ('implementing', 'evaluated', 'concluded', 'abandoned')),
    verdict TEXT CHECK(verdict IN ('supports', 'refutes', 'inconclusive')),
    outcome TEXT CHECK(outcome IN ('baseline', 'improved', 'regressed', 'invalid', 'inconclusive')),
    origin TEXT NOT NULL CHECK(origin IN ('guided', 'auto')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(goal_id, id),
    FOREIGN KEY(goal_id, parent_id) REFERENCES experiments(goal_id, id)
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    goal_id TEXT NOT NULL,
    experiment_id TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    entrypoint TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'failed', 'killed')),
    exit_code INTEGER,
    provenance_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    FOREIGN KEY(goal_id, experiment_id) REFERENCES experiments(goal_id, id)
);
CREATE TABLE IF NOT EXISTS run_parameters (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY(run_id, key)
);
CREATE TABLE IF NOT EXISTS run_metrics (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(run_id, key)
);
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    goal_id TEXT NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    experiment_id TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    parent_experiment_id TEXT,
    status TEXT NOT NULL CHECK(status IN ('pending', 'admitted', 'rejected')),
    created_at TEXT NOT NULL,
    admitted_experiment_id TEXT,
    UNIQUE(goal_id, experiment_id)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_experiments_goal ON experiments(goal_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_baseline_per_goal
    ON experiments(goal_id) WHERE kind = 'baseline';
CREATE INDEX IF NOT EXISTS idx_runs_goal_experiment ON runs(goal_id, experiment_id);
CREATE INDEX IF NOT EXISTS idx_events_entity ON events(entity_type, entity_id);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def migrate(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            row = connection.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                connection.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
            elif row["version"] == 1:
                connection.execute("ALTER TABLE experiments ADD COLUMN outcome TEXT")
                connection.execute("UPDATE schema_version SET version=?", (SCHEMA_VERSION,))
            elif row["version"] != SCHEMA_VERSION:
                raise HirakuError(f"Unsupported state schema {row['version']}; expected {SCHEMA_VERSION}.")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def record_event(
    connection: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    connection.execute(
        "INSERT INTO events(entity_type, entity_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (entity_type, entity_id, event_type, json.dumps(payload or {}, sort_keys=True), utc_now()),
    )
