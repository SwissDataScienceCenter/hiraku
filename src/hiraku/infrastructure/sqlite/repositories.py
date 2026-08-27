from __future__ import annotations

from typing import Any

from ...database import Database, utc_now


class GoalRepository:
    """Persistence operations for Goal records; business validation stays in GoalService."""

    def __init__(self, db: Database):
        self.db = db

    def insert(self, connection, values: tuple[Any, ...]) -> None:
        connection.execute(
            "INSERT INTO goals(id,title,objective,metric,direction,target,evaluation_protocol,"
            "constraints_json,baseline_commit,status,launch_manifest_json,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,'active',?,?,?)",
            values,
        )

    def get(self, goal_id: str):
        with self.db.connect() as connection:
            return connection.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()

    def list(self):
        with self.db.connect() as connection:
            return connection.execute("SELECT * FROM goals ORDER BY created_at, id").fetchall()

    def run_count(self, connection, goal_id: str) -> int:
        return connection.execute("SELECT COUNT(*) FROM runs WHERE goal_id = ?", (goal_id,)).fetchone()[0]

    def update(self, connection, values: tuple[Any, ...], goal_id: str) -> None:
        connection.execute(
            "UPDATE goals SET title=?, objective=?, metric=?, direction=?, evaluation_protocol=?, "
            "constraints_json=?, launch_manifest_json=?, updated_at=? WHERE id=?",
            (*values, goal_id),
        )


class RunRepository:
    """Persistence operations for Runs and their parameter/metric projections."""

    def __init__(self, db: Database):
        self.db = db

    def insert(self, connection, values: tuple[Any, ...]) -> None:
        connection.execute(
            "INSERT INTO runs(id,goal_id,experiment_id,commit_sha,entrypoint,status,exit_code,"
            "provenance_json,started_at,finished_at) VALUES (?,?,?,?,?,'running',NULL,?,?,NULL)",
            values,
        )

    def status(self, connection, run_id: str):
        return connection.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()

    def finalize(self, connection, run_id: str, status: str, exit_code: int | None) -> None:
        connection.execute(
            "UPDATE runs SET status=?,exit_code=?,finished_at=? WHERE id=?",
            (status, exit_code, utc_now(), run_id),
        )

    def parameter(self, connection, run_id: str, key: str):
        return connection.execute("SELECT value FROM run_parameters WHERE run_id=? AND key=?", (run_id, key)).fetchone()

    def insert_parameter(self, connection, run_id: str, key: str, value: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO run_parameters(run_id,key,value) VALUES (?,?,?)", (run_id, key, value)
        )

    def upsert_metric(self, connection, run_id: str, key: str, value: str) -> None:
        connection.execute(
            "INSERT INTO run_metrics(run_id,key,value,updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(run_id,key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (run_id, key, value, utc_now()),
        )

    def list(self, goal_id: str | None, experiment_id: str | None):
        query = "SELECT * FROM runs"
        args: list[str] = []
        where: list[str] = []
        if goal_id:
            where.append("goal_id=?")
            args.append(goal_id)
        if experiment_id:
            where.append("experiment_id=?")
            args.append(experiment_id)
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY started_at,id"
        with self.db.connect() as connection:
            return connection.execute(query, args).fetchall()

    def parameters(self, connection, run_id: str):
        return connection.execute(
            "SELECT key,value FROM run_parameters WHERE run_id=? ORDER BY key", (run_id,)
        ).fetchall()

    def metrics(self, connection, run_id: str):
        return connection.execute("SELECT key,value FROM run_metrics WHERE run_id=? ORDER BY key", (run_id,)).fetchall()
