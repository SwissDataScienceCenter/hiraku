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


@pytest.mark.parametrize("database", [None, "custom.db"])
@pytest.mark.parametrize("directory", [False, True])
def test_mlflow_backend_requires_existing_database(
    tmp_path, monkeypatch, database, directory
) -> None:
    mlflow = pytest.importorskip("mlflow")
    config = HirakuConfig.init(tmp_path)
    path = tmp_path / (database or "mlflow.db")
    if directory:
        path.mkdir()
    before = set(tmp_path.rglob("*"))
    monkeypatch.setattr(
        mlflow.tracking,
        "MlflowClient",
        lambda **kwargs: pytest.fail("client must not be constructed"),
    )

    with pytest.raises(BackendError, match="not an existing file"):
        MLflowBackend(config, database=database).list_runs()

    assert set(tmp_path.rglob("*")) == before
    assert path.is_dir() if directory else not path.exists()


class _Page(list):
    def __init__(self, items, token=None):
        super().__init__(items)
        self.token = token


def _native_run(
    run_id: str, experiment_id: str = "e1", end_time: int | None = 1700000004250
) -> SimpleNamespace:
    return SimpleNamespace(
        info=SimpleNamespace(
            run_id=run_id,
            status="FINISHED",
            artifact_uri="",
            experiment_id=experiment_id,
            start_time=1700000000000,
            end_time=end_time,
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
    (tmp_path / "mlflow.db").touch()

    assert [run.id for run in backend._read_source_runs()] == ["r1", "r2"]
    assert calls == [None, "next"]


def test_mlflow_backend_reads_duration_from_start_and_end(
    tmp_path, monkeypatch
) -> None:
    mlflow = pytest.importorskip("mlflow")

    class FakeClient:
        def __init__(self, tracking_uri: str | None = None) -> None:
            pass

        def search_experiments(self, page_token=None):
            return _Page([SimpleNamespace(experiment_id="e1")])

        def search_runs(self, experiment_ids, max_results, page_token=None):
            return _Page([_native_run("done"), _native_run("running", end_time=None)])

    monkeypatch.setattr(mlflow.tracking, "MlflowClient", FakeClient)
    backend = MLflowBackend(
        HirakuConfig.init(tmp_path), tracking_uri="https://mlflow.example.com"
    )

    assert [run.duration for run in backend._read_source_runs()] == [4.25, None]


@pytest.mark.parametrize(
    "value",
    [
        "not-a-url",
        "ftp://example.com",
        "https://user:secret@example.com",
        "https://example.com/path?query=1",
        "https://example.com/path#fragment",
    ],
)
def test_mlflow_backend_config_rejects_unsafe_ui_url(value: str) -> None:
    with pytest.raises(BackendError, match="ui_url"):
        MLflowBackendConfig(database="mlflow.db", ui_url=value)


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ({"database": "mlflow.db"}, None),
        (
            {"database": "mlflow.db", "ui_url": "http://127.0.0.1:5000"},
            "http://127.0.0.1:5000/#/experiments/e%2F1/runs/r1",
        ),
        (
            {"tracking_uri": "https://mlflow.example.com/prefix/"},
            "https://mlflow.example.com/prefix/#/experiments/e%2F1/runs/r1",
        ),
        (
            {
                "tracking_uri": "https://tracker.example.com",
                "ui_url": "https://ui.example.com",
            },
            "https://ui.example.com/#/experiments/e%2F1/runs/r1",
        ),
        ({"tracking_uri": "https://user:secret@mlflow.example.com"}, None),
    ],
)
def test_mlflow_backend_run_url(tmp_path, settings, expected) -> None:
    backend = MLflowBackend(HirakuConfig.init(tmp_path), **settings)

    assert backend._run_url("e/1", "r1") == expected
