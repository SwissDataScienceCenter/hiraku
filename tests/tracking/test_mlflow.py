from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from hiraku.errors import BackendError
from hiraku.tracking.mlflow import MLflowBackendConfig


def test_read_only_missing_database_does_not_initialize(tmp_path, monkeypatch):
    pytest.importorskip("sqlalchemy")
    from hiraku import HirakuConfig
    from hiraku.tracking.mlflow import MLflowBackend

    config = HirakuConfig.init(tmp_path, install_skill=False)
    backend = MLflowBackend(config)

    def unexpected_read(self, **kwargs):
        pytest.fail("Missing databases must not construct an MLflow client")

    monkeypatch.setattr(MLflowBackend, "_read_source_runs", unexpected_read)
    before = {p.relative_to(tmp_path) for p in tmp_path.rglob("*")}
    assert backend.list_runs(read_only=True) == []
    assert {p.relative_to(tmp_path) for p in tmp_path.rglob("*")} == before


@pytest.mark.parametrize("wal", [False, True])
def test_read_only_queries_snapshot_not_original(tmp_path, monkeypatch, wal):
    pytest.importorskip("sqlalchemy")
    from hiraku import HirakuConfig, Run
    from hiraku.tracking.mlflow import MLflowBackend

    config = HirakuConfig.init(tmp_path, install_skill=False)
    database = tmp_path / "mlflow.db"
    with sqlite3.connect(database) as db:
        if wal:
            db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE original (value TEXT)")
        db.execute("INSERT INTO original VALUES ('preserved')")

    def project_files():
        return {
            p.relative_to(tmp_path): p.read_bytes()
            for p in tmp_path.rglob("*")
            if p.is_file()
        }

    before = project_files()
    snapshots = []

    def migrating_read(self, **kwargs):
        snapshot = Path(self.tracking_uri.removeprefix("sqlite:///"))
        snapshots.append(snapshot)
        assert snapshot != database
        with sqlite3.connect(snapshot) as db:
            assert db.execute("SELECT value FROM original").fetchone() == ("preserved",)
            db.execute("CREATE TABLE migration (value TEXT)")
        return [Run(id="run", record_id="record", metrics={"score": 1})]

    monkeypatch.setattr(MLflowBackend, "_read_source_runs", migrating_read)
    runs = MLflowBackend(config).list_runs(read_only=True, record_id="record", limit=1)
    assert len(runs) == 1
    assert project_files() == before
    assert not snapshots[0].exists()


def test_mlflow_backend_config_accepts_uri_or_database() -> None:
    assert MLflowBackendConfig(tracking_uri="http://localhost:5000").tracking_uri
    assert MLflowBackendConfig(database="mlflow.db").database is not None


def test_real_mlflow_read_only_query_preserves_database(tmp_path):
    pytest.importorskip("mlflow")
    from mlflow.tracking import MlflowClient

    from hiraku import HirakuConfig
    from hiraku.tracking.mlflow import MLflowBackend

    config = HirakuConfig.init(tmp_path, install_skill=False)
    client = MlflowClient(tracking_uri=f"sqlite:///{tmp_path / 'mlflow.db'}")
    experiment = client.create_experiment("readonly-test")
    run = client.create_run(experiment, tags={"hiraku.record_id": "record"})
    client.log_metric(run.info.run_id, "score", 0.5)
    client.set_terminated(run.info.run_id)
    before = {
        p.relative_to(tmp_path): p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }
    runs = MLflowBackend(config, experiment_name="readonly-test").list_runs(
        read_only=True
    )
    assert [(r.record_id, r.metrics["score"]) for r in runs] == [("record", 0.5)]
    assert {
        p.relative_to(tmp_path): p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file()
    } == before


def test_mlflow_backend_config_rejects_uri_and_database() -> None:
    with pytest.raises(BackendError, match="both"):
        MLflowBackendConfig(tracking_uri="http://localhost:5000", database="mlflow.db")
