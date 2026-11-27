from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from hiraku.errors import BackendError
from hiraku.project import Project
from hiraku.tracking import Leaderboard, MLflowBackend, MLflowBackendConfig


def stub_mlflow_client(monkeypatch, factory):
    monkeypatch.setitem(
        sys.modules, "mlflow.tracking", SimpleNamespace(MlflowClient=factory)
    )


@pytest.fixture(autouse=True)
def clear_mlflow_environment(monkeypatch):
    for name in (
        "MLFLOW_TRACKING_URI",
        "MLFLOW_EXPERIMENT_NAME",
        "MLFLOW_EXPERIMENT_ID",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    "options",
    [{}, {"tracking_uri": "https://explicit.example"}],
)
def test_mlflow_destination_precedence(tmp_path, monkeypatch, options):
    stub_mlflow_client(
        monkeypatch,
        lambda **kwargs: SimpleNamespace(get_experiment_by_name=lambda _: None),
    )
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "https://environment.example")
    backend = MLflowBackend(Project(tmp_path), **options)
    expected = options.get("tracking_uri", "https://environment.example")
    assert backend.tracking_uri == expected
    assert backend.ui_url == expected


def test_explicit_server_overrides_invalid_environment(tmp_path, monkeypatch):
    stub_mlflow_client(
        monkeypatch,
        lambda **kwargs: SimpleNamespace(get_experiment_by_name=lambda _: None),
    )
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "sqlite:///runs.db")
    backend = MLflowBackend(Project(tmp_path), tracking_uri="https://explicit.example")
    assert backend.tracking_uri == "https://explicit.example"


@pytest.mark.parametrize(
    ("name", "experiment_id", "explicit", "error"),
    [
        ("shared", None, None, None),
        (None, "e1", None, None),
        ("shared", "e1", None, None),
        ("shared", "e2", None, "different experiments"),
        ("missing", None, None, None),
        (None, "missing", None, "unknown experiment"),
        ("shared", "e2", "explicit", None),
    ],
)
def test_mlflow_experiment_environment(
    tmp_path, monkeypatch, name, experiment_id, explicit, error
):
    mlflow = pytest.importorskip("mlflow")
    for key, value in (
        ("MLFLOW_EXPERIMENT_NAME", name),
        ("MLFLOW_EXPERIMENT_ID", experiment_id),
    ):
        if value is not None:
            monkeypatch.setenv(key, value)
    searched = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def get_experiment_by_name(self, value):
            assert value == (explicit or name)
            return None if value == "missing" else SimpleNamespace(experiment_id="e1")

        def get_experiment(self, value):
            assert value == experiment_id
            if value == "missing":
                raise RuntimeError("unknown experiment")
            return SimpleNamespace(experiment_id=value)

        def search_runs(self, experiment_ids, **kwargs):
            searched.extend(experiment_ids)
            return []

    monkeypatch.setattr(mlflow.tracking, "MlflowClient", FakeClient)
    def create_backend():
        return MLflowBackend(
            Project(tmp_path),
            tracking_uri="https://example.com",
            experiment_name=explicit,
        )

    if error:
        with pytest.raises(BackendError, match=error):
            create_backend()
    else:
        assert create_backend().list_runs() == []
    assert searched == ([] if error or name == "missing" else ["e1"])


def test_mlflow_backend_reuses_connection(tmp_path, monkeypatch):
    connections = []
    writes = []
    client = SimpleNamespace(
        get_experiment_by_name=lambda name: SimpleNamespace(experiment_id="e1"),
        search_runs=lambda **kwargs: [],
        set_tag=lambda *args: writes.append(args),
    )

    def create_client(*, tracking_uri):
        connections.append(tracking_uri)
        return client

    stub_mlflow_client(monkeypatch, create_client)
    backend = MLflowBackend(
        Project(tmp_path),
        tracking_uri="https://example.com",
        experiment_name="shared",
    )
    assert connections == ["https://example.com"]
    assert backend.list_runs() == []
    backend.set_tag("run-1", "hiraku.record_id", "experiments/new")
    assert writes == [("run-1", "hiraku.record_id", "experiments/new")]
    assert connections == ["https://example.com"]


@pytest.mark.integration
def test_training_and_leaderboard_share_mlflow_environment(
    tmp_path, monkeypatch, mlflow_server
):
    mlflow = pytest.importorskip("mlflow")
    from mlflow.tracking._tracking_service import utils

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(utils, "_tracking_uri", None)
    monkeypatch.setattr(mlflow.tracking.fluent, "_active_experiment_id", None)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", mlflow_server)
    monkeypatch.setenv("MLFLOW_EXPERIMENT_NAME", "shared")
    with mlflow.start_run() as active:
        mlflow.set_tag("hiraku.record_id", "baseline")
        mlflow.log_metric("loss", 0.5)
    runs = Leaderboard(source={"type": "mlflow"}).list_runs(Project(tmp_path))
    assert [run.id for run in runs] == [active.info.run_id]
    assert runs[0].record_id == "baseline" and runs[0].metrics["loss"] == 0.5


@pytest.mark.parametrize(
    "uri",
    ["http://localhost:5000", "https://example.com/prefix", "http://[::1]:5000"],
)
def test_mlflow_backend_config_accepts_server_url(uri):
    assert MLflowBackendConfig(tracking_uri=uri).tracking_uri == uri


@pytest.mark.parametrize("environment", [False, True])
@pytest.mark.parametrize(
    "uri",
    [
        "sqlite:///runs.db",
        "file:///runs",
        "./mlruns",
        "databricks",
        "ftp://example.com",
        "http:///path",
        "https://[::1",
        "http://example.com:invalid",
        "https://example.com:99999",
        "https://example.com/?query=1",
        "https://example.com/#fragment",
        "https://exa mple.com",
        "https://example.com\n",
    ],
)
def test_mlflow_requires_http_server_before_client_creation(
    tmp_path, monkeypatch, environment, uri
):
    monkeypatch.setitem(sys.modules, "mlflow.tracking", None)
    options = {} if environment else {"tracking_uri": uri}
    if environment:
        monkeypatch.setenv("MLFLOW_TRACKING_URI", uri)
    before = set(tmp_path.rglob("*"))
    with pytest.raises(BackendError, match="HTTP\\(S\\)"):
        MLflowBackend(Project(tmp_path), **options).list_runs()
    assert set(tmp_path.rglob("*")) == before


def test_mlflow_backend_requires_explicit_server(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "mlflow.tracking", None)
    with pytest.raises(BackendError, match="tracking_uri or MLFLOW_TRACKING_URI"):
        MLflowBackend(Project(tmp_path)).list_runs()
    assert list(tmp_path.iterdir()) == []


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
        data=SimpleNamespace(
            tags={"hiraku.record_id": "root", "hiraku.job_id": "b" * 32},
            metrics={},
            params={},
        ),
    )


def test_mlflow_backend_paginates_runs_in_named_experiment(
    tmp_path, monkeypatch
) -> None:
    mlflow = pytest.importorskip("mlflow")
    calls: list[str | None] = []

    class FakeClient:
        def __init__(self, tracking_uri: str | None = None) -> None:
            pass

        def get_experiment_by_name(self, name):
            assert name == tmp_path.name
            return SimpleNamespace(experiment_id="e1")

        def search_runs(self, experiment_ids, max_results, page_token=None):
            assert experiment_ids == ["e1"]
            calls.append(page_token)
            if page_token is None:
                return _Page([_native_run("r1")], "next")
            return _Page([_native_run("r2")])

    monkeypatch.setattr(mlflow.tracking, "MlflowClient", FakeClient)
    backend = MLflowBackend(
        Project.create(tmp_path), tracking_uri="https://mlflow.example.com"
    )

    assert [run.id for run in backend._read_source_runs()] == ["r1", "r2"]
    assert calls == [None, "next"]


def test_mlflow_backend_reads_duration_from_start_and_end(
    tmp_path, monkeypatch
) -> None:
    mlflow = pytest.importorskip("mlflow")

    class FakeClient:
        def __init__(self, tracking_uri: str | None = None) -> None:
            pass

        def get_experiment_by_name(self, name):
            assert name == tmp_path.name
            return SimpleNamespace(experiment_id="e1")

        def search_runs(self, experiment_ids, max_results, page_token=None):
            return _Page([_native_run("done"), _native_run("running", end_time=None)])

    monkeypatch.setattr(mlflow.tracking, "MlflowClient", FakeClient)
    backend = MLflowBackend(
        Project.create(tmp_path), tracking_uri="https://mlflow.example.com"
    )

    assert [run.duration for run in backend._read_source_runs()] == [4.25, None]
    assert [run.job_id for run in backend._read_source_runs()] == ["b" * 32] * 2


def test_mlflow_backend_reports_missing_optional_dependency(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setitem(sys.modules, "mlflow.tracking", None)
    with pytest.raises(BackendError, match="optional 'mlflow' dependency"):
        MLflowBackend(
            Project.create(tmp_path), tracking_uri="https://mlflow.example.com"
        )


def test_mlflow_backend_returns_no_runs_for_unknown_experiment(
    tmp_path, monkeypatch
) -> None:
    mlflow = pytest.importorskip("mlflow")

    class FakeClient:
        def __init__(self, tracking_uri: str | None = None) -> None:
            pass

        def get_experiment_by_name(self, name):
            assert name == tmp_path.name
            return None

        def search_runs(self, **kwargs):
            pytest.fail("runs must not be queried for an unknown experiment")

    monkeypatch.setattr(mlflow.tracking, "MlflowClient", FakeClient)
    backend = MLflowBackend(
        Project.create(tmp_path), tracking_uri="https://mlflow.example.com"
    )

    assert backend._read_source_runs() == []


def test_mlflow_backend_wraps_client_query_errors(tmp_path, monkeypatch) -> None:
    mlflow = pytest.importorskip("mlflow")

    class FakeClient:
        def __init__(self, tracking_uri: str | None = None) -> None:
            pass

        def get_experiment_by_name(self, name):
            return SimpleNamespace(experiment_id="e1")

        def search_runs(self, **kwargs):
            raise RuntimeError("tracking service unavailable")

    monkeypatch.setattr(mlflow.tracking, "MlflowClient", FakeClient)
    backend = MLflowBackend(
        Project.create(tmp_path), tracking_uri="https://mlflow.example.com"
    )

    with pytest.raises(BackendError, match="mlflow backend query failed"):
        backend._read_source_runs()


def test_mlflow_backend_preserves_backend_errors_from_client(
    tmp_path, monkeypatch
) -> None:
    mlflow = pytest.importorskip("mlflow")

    class FakeClient:
        def __init__(self, tracking_uri: str | None = None) -> None:
            pass

        def get_experiment_by_name(self, name):
            return SimpleNamespace(experiment_id="e1")

        def search_runs(self, **kwargs):
            raise BackendError("specific backend failure")

    monkeypatch.setattr(mlflow.tracking, "MlflowClient", FakeClient)
    backend = MLflowBackend(
        Project.create(tmp_path), tracking_uri="https://mlflow.example.com"
    )

    with pytest.raises(BackendError, match="specific backend failure"):
        backend._read_source_runs()


@pytest.mark.parametrize(
    "value",
    [
        "not-a-url",
        "https://[::1",
        "ftp://example.com",
        "https://user:secret@example.com",
        "https://example.com/path?query=1",
        "https://example.com/path#fragment",
    ],
)
def test_mlflow_backend_config_rejects_unsafe_ui_url(value: str) -> None:
    with pytest.raises(BackendError, match="ui_url"):
        MLflowBackendConfig(ui_url=value)


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
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
def test_mlflow_backend_run_url(tmp_path, monkeypatch, settings, expected) -> None:
    stub_mlflow_client(
        monkeypatch,
        lambda **kwargs: SimpleNamespace(get_experiment_by_name=lambda _: None),
    )
    backend = MLflowBackend(Project.create(tmp_path), **settings)

    assert backend._run_url("e/1", "r1") == expected
