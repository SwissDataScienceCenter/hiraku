from __future__ import annotations

from types import SimpleNamespace

import pytest

from hiraku.config import HirakuConfig
from hiraku.errors import BackendError
from hiraku.tracking.mlflow import MLflowBackend, MLflowBackendConfig


def test_mlflow_backend_config_accepts_uri_or_database() -> None:
    assert MLflowBackendConfig(tracking_uri="http://localhost:5000").tracking_uri
    assert MLflowBackendConfig(database="mlflow.db").database is not None


def test_mlflow_backend_config_rejects_uri_and_database() -> None:
    with pytest.raises(BackendError, match="both"):
        MLflowBackendConfig(tracking_uri="http://localhost:5000", database="mlflow.db")


class _Page(list):
    def __init__(self, items, token=None):
        super().__init__(items)
        self.token = token


def _native_run(run_id: str, experiment_id: str = "e1") -> SimpleNamespace:
    return SimpleNamespace(
        info=SimpleNamespace(
            run_id=run_id,
            status="FINISHED",
            artifact_uri="",
            experiment_id=experiment_id,
            start_time=1700000000000,
        ),
        data=SimpleNamespace(tags={"hiraku.record_id": "root"}, metrics={}, params={}),
    )


def test_mlflow_backend_paginates_experiments_and_runs(tmp_path, monkeypatch) -> None:
    mlflow = pytest.importorskip("mlflow")
    calls: list[str | None] = []

    class FakeClient:
        def __init__(self, tracking_uri: str | None = None) -> None:
            pass

        def search_experiments(self, page_token=None):
            if page_token is None:
                return _Page([SimpleNamespace(experiment_id="e1")], "more")
            return _Page([SimpleNamespace(experiment_id="e2")])

        def search_runs(self, experiment_ids, max_results, page_token=None):
            assert experiment_ids == ["e1", "e2"]
            calls.append(page_token)
            if page_token is None:
                return _Page([_native_run("r1")], "next")
            return _Page([_native_run("r2")])

    monkeypatch.setattr(mlflow.tracking, "MlflowClient", FakeClient)
    backend = MLflowBackend(HirakuConfig.init(tmp_path), database="mlflow.db")

    assert [run.id for run in backend._read_source_runs()] == ["r1", "r2"]
    assert calls == [None, "next"]
