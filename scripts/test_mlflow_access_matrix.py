"""Verify the matrix observer does not itself create or alter databases."""

import runpy
import sqlite3
from pathlib import Path

matrix = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts/mlflow_access_matrix.py")
)
snapshot = matrix["snapshot"]
changes = matrix["changes"]


def test_snapshot_missing_database(tmp_path):
    assert snapshot(tmp_path) == {"exists": False, "files": {}}
    assert list(tmp_path.iterdir()) == []


def test_snapshot_empty_database(tmp_path):
    database = tmp_path / "mlflow.db"
    database.touch()
    before = database.read_bytes()
    observed = snapshot(tmp_path)
    assert observed["exists"]
    assert observed["schema"] == []
    assert database.read_bytes() == before
    assert list(tmp_path.iterdir()) == [database]


def test_snapshot_detects_migration_data_and_files(tmp_path):
    database = tmp_path / "mlflow.db"
    before = snapshot(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT)")
        connection.execute("INSERT INTO alembic_version VALUES ('old')")
    initialized = snapshot(tmp_path)
    assert changes(before, initialized)["created"]
    original_bytes = database.read_bytes()
    assert snapshot(tmp_path) == initialized
    assert database.read_bytes() == original_bytes
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE alembic_version SET version_num = 'new'")
        connection.execute("CREATE TABLE runs (id TEXT)")
    (tmp_path / "artifact").write_text("example")
    delta = changes(initialized, snapshot(tmp_path))
    assert not delta["created"]
    assert delta["schema_changed"]
    assert delta["revision_changed"]
    assert delta["rows_changed"]
    assert delta["files_changed"] == ["artifact", "mlflow.db"]
