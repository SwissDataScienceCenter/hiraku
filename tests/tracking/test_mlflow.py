from __future__ import annotations

import pytest

from hiraku.errors import BackendError
from hiraku.tracking.mlflow import MLflowBackendConfig


def test_mlflow_backend_config_accepts_uri_or_database() -> None:
    assert MLflowBackendConfig(tracking_uri="http://localhost:5000").tracking_uri
    assert MLflowBackendConfig(database="mlflow.db").database is not None


def test_mlflow_backend_config_rejects_uri_and_database() -> None:
    with pytest.raises(BackendError, match="both"):
        MLflowBackendConfig(tracking_uri="http://localhost:5000", database="mlflow.db")


def test_mlflow_backend_paginates_run_search(tmp_path, monkeypatch) -> None:
    mlflow = pytest.importorskip("mlflow")
    from types import SimpleNamespace

    from hiraku.config import HirakuConfig
    from hiraku.tracking.mlflow import MLflowBackend

    config = HirakuConfig.init(tmp_path)

    def make_run(run_id: str):
        return SimpleNamespace(
            info=SimpleNamespace(
                run_id=run_id,
                status="FINISHED",
                artifact_uri="",
                experiment_id="e1",
                start_time=1700000000000,
            ),
            data=SimpleNamespace(
                tags={"hiraku.record_id": "root"},
                metrics={},
                params={},
            ),
        )

    class Page(list):
        def __init__(self, items, token):
            super().__init__(items)
            self.token = token

    calls: list[str | None] = []

    class FakeClient:
        def __init__(self, tracking_uri: str | None = None) -> None:
            pass

        def search_experiments(self):
            return [SimpleNamespace(experiment_id="e1")]

        def search_runs(self, experiment_ids=None, max_results=1000, page_token=None):
            calls.append(page_token)
            if page_token is None:
                return Page([make_run("r1")], "next")
            return Page([make_run("r2")], None)

    monkeypatch.setattr(mlflow.tracking, "MlflowClient", FakeClient)

    runs = MLflowBackend(config, tracking_uri="sqlite:///unused")._read_source_runs()

    assert [run.id for run in runs] == ["r1", "r2"]
    assert calls == [None, "next"]
