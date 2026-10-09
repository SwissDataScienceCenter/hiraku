from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.errors import BackendError
from hiraku.tracking.mlflow import (
    MLflowBackend,
    MLflowBackendConfig,
    _resolve_ui_url,
    _validate_ui_url,
)


def test_mlflow_backend_config_accepts_uri_or_database() -> None:
    assert MLflowBackendConfig(tracking_uri="http://localhost:5000").tracking_uri
    assert MLflowBackendConfig(database="mlflow.db").database is not None


def test_mlflow_backend_config_rejects_uri_and_database() -> None:
    with pytest.raises(BackendError, match="both"):
        MLflowBackendConfig(tracking_uri="http://localhost:5000", database="mlflow.db")


@pytest.mark.parametrize(
    "value",
    [
        "http://localhost:5000",
        "https://mlflow.example.com:8080/deployment/prefix/",
        "https://mlflow.example.com",
    ],
)
def test_mlflow_ui_url_accepts_valid_web_bases(value: str) -> None:
    assert _validate_ui_url(value) == value


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
def test_mlflow_ui_url_rejects_unsafe_or_invalid_bases(value: str) -> None:
    with pytest.raises(BackendError):
        _validate_ui_url(value)


def test_mlflow_backend_explicit_ui_url_takes_precedence(
    tmp_path: Path,
) -> None:
    config = HirakuConfig.init(tmp_path)
    backend = MLflowBackend(
        config,
        tracking_uri="https://tracker.example.com",
        ui_url="https://ui.example.com",
    )
    assert backend.ui_url == "https://ui.example.com"
    assert backend._run_url("e1", "r1") == (
        "https://ui.example.com/#/experiments/e1/runs/r1"
    )


def test_mlflow_backend_http_tracking_uri_falls_back_as_ui_base(
    tmp_path: Path,
) -> None:
    config = HirakuConfig.init(tmp_path)
    backend = MLflowBackend(config, tracking_uri="https://mlflow.example.com:5000")
    assert backend.ui_url == "https://mlflow.example.com:5000"


def test_mlflow_backend_sqlite_has_no_ui_url_without_explicit_setting(
    tmp_path: Path,
) -> None:
    config = HirakuConfig.init(tmp_path)
    backend = MLflowBackend(config, database="mlflow.db")
    assert backend.ui_url is None
    assert backend._run_url("e1", "r1") is None


def test_mlflow_backend_credential_tracking_uri_has_no_ui_url(
    tmp_path: Path,
) -> None:
    config = HirakuConfig.init(tmp_path)
    backend = MLflowBackend(
        config, tracking_uri="https://user:secret@mlflow.example.com"
    )
    assert backend.ui_url is None


def test_mlflow_run_url_preserves_prefix_and_encodes_identifiers(
    tmp_path: Path,
) -> None:
    config = HirakuConfig.init(tmp_path)
    backend = MLflowBackend(
        config,
        tracking_uri="https://tracker.example.com",
        ui_url="https://ui.example.com:8080/mlflow/",
    )
    assert backend._run_url("experiment/1", "run/1") == (
        "https://ui.example.com:8080/mlflow/#/experiments/experiment%2F1/runs/run%2F1"
    )


def test_mlflow_resolve_ui_url_prefers_explicit_over_uri() -> None:
    assert _resolve_ui_url("https://tracker.example.com", None) == (
        "https://tracker.example.com"
    )
    assert _resolve_ui_url("sqlite:///mlflow.db", "https://ui.example.com") == (
        "https://ui.example.com"
    )
    assert _resolve_ui_url("sqlite:///mlflow.db", None) is None
    assert _resolve_ui_url("https://user:secret@example.com", None) is None


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

        def search_experiments(self, page_token=None):
            if page_token is None:
                return Page([SimpleNamespace(experiment_id="e1")], "experiments-next")
            assert page_token == "experiments-next"
            return Page([SimpleNamespace(experiment_id="e2")], None)

        def search_runs(self, experiment_ids=None, max_results=1000, page_token=None):
            assert experiment_ids == ["e1", "e2"]
            calls.append(page_token)
            if page_token is None:
                return Page([make_run("r1")], "next")
            return Page([make_run("r2")], None)

    monkeypatch.setattr(mlflow.tracking, "MlflowClient", FakeClient)

    runs = MLflowBackend(config, tracking_uri="sqlite:///unused")._read_source_runs()

    assert [run.id for run in runs] == ["r1", "r2"]
    assert calls == [None, "next"]


def test_mlflow_read_only_clone_forwards_ui_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mlflow = pytest.importorskip("mlflow")
    from types import SimpleNamespace

    config = HirakuConfig.init(tmp_path)
    backend = MLflowBackend(
        config,
        database="mlflow.db",
        ui_url="https://ui.example.com",
    )

    class Page(list):
        def __init__(self, items):
            super().__init__(items)
            self.token = None

    class FakeClient:
        def __init__(self, tracking_uri: str | None = None) -> None:
            pass

        def search_experiments(self, page_token=None):
            return [SimpleNamespace(experiment_id="e1")]

        def search_runs(self, experiment_ids=None, max_results=1000, page_token=None):
            return Page(
                [
                    SimpleNamespace(
                        info=SimpleNamespace(
                            run_id="r1",
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
                ]
            )

    monkeypatch.setattr(mlflow.tracking, "MlflowClient", FakeClient)

    @contextmanager
    def fake_uri():
        yield "https://tracker.example.com"

    monkeypatch.setattr(backend, "_read_only_uri", fake_uri)

    runs = backend._read_source_runs(read_only=True)

    assert runs[0].url == "https://ui.example.com/#/experiments/e1/runs/r1"
