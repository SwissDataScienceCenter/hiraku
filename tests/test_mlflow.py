from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest
import yaml
from conftest import hiraku, run

from hiraku.config import load_project
from hiraku.errors import HirakuError
from hiraku.tracking import MlflowBackend

mlflow = pytest.importorskip("mlflow")
PROJECT_ROOT = Path(__file__).parents[1]


def sqlite_uri(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def configure_mlflow(repository: Path, task_id: str, experiment_name: str) -> None:
    config_path = repository / ".hiraku.yml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["tracking_backend"] = "mlflow"
    config["mlflow_tracking_db"] = "tracking/mlflow.db"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    hiraku(repository, "task", "create", task_id)
    task_path = repository / f"tasks/{task_id}/task.md"
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace(
            'branch: "hiraku/task/',
            f'mlflow_experiment: "{experiment_name}"\nbranch: "hiraku/task/',
        ),
        encoding="utf-8",
    )


def test_mlflow_filtering_all_and_ranking(
    repository: Path,
) -> None:
    configure_mlflow(repository, "baseline", "baseline-runs")
    hiraku(repository, "exp", "create", "baseline:trial")
    tracking_uri = sqlite_uri(repository / "tracking/mlflow.db")
    client = mlflow.tracking.MlflowClient(tracking_uri=tracking_uri)
    experiment_id = client.create_experiment("baseline-runs")

    valid = client.create_run(experiment_id)
    client.log_param(valid.info.run_id, "task_id", "baseline")
    client.log_param(valid.info.run_id, "experiment_id", "trial")
    client.log_param(valid.info.run_id, "commit", "a" * 40)
    client.log_metric(valid.info.run_id, "score", 0.25, timestamp=1, step=0)
    client.log_metric(valid.info.run_id, "score", 0.75, timestamp=2, step=1)
    client.log_metric(valid.info.run_id, "loss", 0.125)
    client.set_tag(valid.info.run_id, "purpose", "test")
    client.set_terminated(valid.info.run_id)

    invalid = client.create_run(experiment_id)
    client.log_metric(invalid.info.run_id, "score", 0.9)
    client.set_terminated(invalid.info.run_id)

    default = hiraku(
        repository,
        "runs",
        "baseline",
        "--sort",
        "metrics.score",
        "--descending",
        "--json",
    )
    rows = json.loads(default.stdout)
    assert [row["run_id"] for row in rows] == [valid.info.run_id]
    assert rows[0]["metrics.score"] == 0.75
    assert rows[0]["_rank"] == 1
    assert "Omitted 1 MLflow run" in default.stderr

    all_runs = hiraku(repository, "runs", "baseline", "--all", "--json")
    rows = json.loads(all_runs.stdout)
    assert {row["run_id"] for row in rows} == {valid.info.run_id, invalid.info.run_id}
    invalid_row = next(row for row in rows if row["run_id"] == invalid.info.run_id)
    assert "missing task_id" in invalid_row["_provenance_warnings"]

    compact = hiraku(repository, "runs", "baseline", "--metrics", "score")
    compact_header = compact.stdout.splitlines()[0]
    assert "score" in compact_header
    assert "loss" not in compact_header
    assert "params" not in compact_header
    assert "tags" not in compact_header
    assert "task_id" not in compact_header
    assert valid.info.run_id[:8] in compact.stdout
    assert valid.info.run_id not in compact.stdout
    assert "a" * 10 in compact.stdout
    assert "a" * 40 not in compact.stdout

    verbose = hiraku(repository, "runs", "baseline", "--verbose").stdout
    verbose_header = verbose.splitlines()[0]
    assert "params.task_id" in verbose_header
    assert "metrics.score" in verbose_header
    assert "tags.purpose" in verbose_header
    assert "  params  " not in verbose_header
    assert valid.info.run_id in verbose


def test_dashboard_normalizes_local_mlflow_runs(repository: Path) -> None:
    pytest.importorskip("altair")
    pytest.importorskip("bleach")
    pytest.importorskip("markdown")
    from hiraku.dashboard import build_dashboard, filter_dashboard

    configure_mlflow(repository, "baseline", "baseline-dashboard-runs")
    hiraku(repository, "exp", "create", "baseline:trial", "--status", "evaluated")
    empty = build_dashboard(load_project(repository), "baseline")
    assert empty["runs"] == []
    assert "does not exist yet" in empty["warnings"][0]
    tracking_uri = sqlite_uri(repository / "tracking/mlflow.db")
    client = mlflow.tracking.MlflowClient(tracking_uri=tracking_uri)
    experiment_id = client.create_experiment("baseline-dashboard-runs")
    recorded = client.create_run(experiment_id)
    client.log_param(recorded.info.run_id, "task_id", "baseline")
    client.log_param(recorded.info.run_id, "experiment_id", "trial")
    client.log_param(recorded.info.run_id, "commit", "b" * 40)
    client.log_param(recorded.info.run_id, "seed", "42")
    client.log_metric(recorded.info.run_id, "score", 0.81)
    client.set_terminated(recorded.info.run_id)

    snapshot = build_dashboard(load_project(repository), "baseline")
    assert snapshot["tracking_backend"] == "mlflow"
    assert snapshot["metrics"] == ["score"]
    assert snapshot["runs"][0]["params"]["seed"] == "42"
    assert snapshot["runs"][0]["metrics"] == {"score": 0.81}
    view = filter_dashboard(snapshot, metric="score")
    assert view["frontier"][0]["frontier"] == 0.81


def test_mlflow_launch_environment_uses_original_project_store(repository: Path) -> None:
    configure_mlflow(repository, "baseline", "baseline-runs")
    script = repository / "capture.py"
    script.write_text(
        """import json
import os
from pathlib import Path

keys = [
    "HIRAKU_MLFLOW_TRACKING_URI",
    "HIRAKU_MLFLOW_EXPERIMENT",
    "MLFLOW_TRACKING_URI",
    "MLFLOW_EXPERIMENT_NAME",
]
Path("environment.json").write_text(json.dumps({key: os.environ.get(key) for key in keys}))
""",
        encoding="utf-8",
    )
    launch = {"entrypoints": {"capture": {"command": ["python", "capture.py"], "cwd": "."}}}
    (repository / "tasks/baseline/launch.yml").write_text(
        yaml.safe_dump(launch, sort_keys=False), encoding="utf-8"
    )
    hiraku(repository, "launch", "baseline:capture")
    values = json.loads((repository / "environment.json").read_text(encoding="utf-8"))
    expected = sqlite_uri(repository / "tracking/mlflow.db")
    assert values["HIRAKU_MLFLOW_TRACKING_URI"] == expected
    assert values["MLFLOW_TRACKING_URI"] == expected
    assert values["HIRAKU_MLFLOW_EXPERIMENT"] == "baseline-runs"


def test_remote_mlflow_uri_is_injected_without_local_fallback(repository: Path) -> None:
    config_path = repository / ".hiraku.yml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config.update(
        {
            "tracking_backend": "mlflow",
            "mlflow_tracking_uri": "https://mlflow.example.org/api",
        }
    )
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    hiraku(repository, "task", "create", "remote")
    task_path = repository / "tasks/remote/task.md"
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace(
            'branch: "hiraku/task/remote"',
            'mlflow_experiment: "remote-runs"\nbranch: "hiraku/task/remote"',
        ),
        encoding="utf-8",
    )
    script = repository / "capture_remote.py"
    script.write_text(
        """import json
import os
from pathlib import Path

Path("remote-environment.json").write_text(json.dumps({
    "hiraku": os.environ["HIRAKU_MLFLOW_TRACKING_URI"],
    "mlflow": os.environ["MLFLOW_TRACKING_URI"],
    "table": os.environ.get("HIRAKU_TABLE_PATH"),
}))
""",
        encoding="utf-8",
    )
    launch = {"entrypoints": {"capture": {"command": ["python", "capture_remote.py"], "cwd": "."}}}
    (repository / "tasks/remote/launch.yml").write_text(
        yaml.safe_dump(launch, sort_keys=False), encoding="utf-8"
    )
    hiraku(repository, "launch", "remote:capture")
    values = json.loads((repository / "remote-environment.json").read_text(encoding="utf-8"))
    assert values == {
        "hiraku": "https://mlflow.example.org/api",
        "mlflow": "https://mlflow.example.org/api",
        "table": None,
    }


def test_remote_mlflow_error_is_actionable_and_redacted(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = repository / ".hiraku.yml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config.update(
        {
            "tracking_backend": "mlflow",
            "mlflow_tracking_uri": "https://mlflow.example.org",
        }
    )
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    hiraku(repository, "task", "create", "remote")
    task_path = repository / "tasks/remote/task.md"
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace(
            'branch: "hiraku/task/remote"',
            'mlflow_experiment: "remote-runs"\nbranch: "hiraku/task/remote"',
        ),
        encoding="utf-8",
    )

    def fail_query(*args: object, **kwargs: object) -> object:
        raise mlflow.exceptions.MlflowException("server exposed secret-token")

    monkeypatch.setattr(mlflow.tracking.MlflowClient, "get_experiment_by_name", fail_query)
    backend = MlflowBackend(load_project(repository))
    with pytest.raises(HirakuError, match="connectivity.*authentication") as raised:
        backend.read_runs("remote")
    assert "secret-token" not in str(raised.value)


def test_autoresearch_cpu_mock_supports_mlflow(tmp_path: Path) -> None:
    source = Path(__file__).parents[1] / "examples/autoresearch"
    repository = tmp_path / "autoresearch-mlflow"
    shutil.copytree(source, repository)
    run(repository, "git", "init", "-b", "main")
    run(repository, "git", "config", "user.email", "tests@example.com")
    run(repository, "git", "config", "user.name", "Hiraku Tests")
    hiraku(repository, "init")
    run(repository, "git", "add", ".")
    run(repository, "git", "commit", "-m", "initialize example")
    config_path = repository / ".hiraku.yml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["tracking_backend"] = "mlflow"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    hiraku(repository, "task", "create", "autoresearch", "--template", "autoresearch")
    task_path = repository / "tasks/autoresearch/task.md"
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace(
            'branch: "hiraku/task/autoresearch"',
            'mlflow_experiment: "autoresearch-runs"\nbranch: "hiraku/task/autoresearch"',
        ),
        encoding="utf-8",
    )
    run(repository, "git", "switch", "hiraku/task/autoresearch")
    run(repository, "git", "add", ".")
    run(repository, "git", "commit", "-m", "configure autoresearch task")
    hiraku(
        repository,
        "exp",
        "create",
        "autoresearch:contract",
        "--entrypoint",
        "cpu-mock --val-bpb 0.8",
    )
    hiraku(repository, "exp", "set", "autoresearch:contract", "--commit", "HEAD")
    hiraku(
        repository,
        "launch",
        "--experiment",
        "contract",
        "autoresearch:cpu-mock",
        "--val-bpb",
        "0.8",
    )
    rows = json.loads(hiraku(repository, "runs", "autoresearch", "--json").stdout)
    assert rows[0]["metrics.val_bpb"] == 0.8


def test_smoke_example_launches_to_mlflow(tmp_path: Path) -> None:
    repository = tmp_path / "smoke-mlflow"
    shutil.copytree(PROJECT_ROOT / "examples/smoke", repository)
    run(repository, "git", "init", "-b", "main")
    run(repository, "git", "config", "user.email", "tests@example.com")
    run(repository, "git", "config", "user.name", "Hiraku Tests")
    hiraku(repository, "init")
    run(repository, "git", "add", ".")
    run(repository, "git", "commit", "-m", "initialize example")
    config_path = repository / ".hiraku.yml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["tracking_backend"] = "mlflow"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    hiraku(repository, "task", "create", "smoke", "--template", "smoke")
    task_path = repository / "tasks/smoke/task.md"
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace(
            'branch: "hiraku/task/smoke"',
            'mlflow_experiment: "smoke-runs"\nbranch: "hiraku/task/smoke"',
        ),
        encoding="utf-8",
    )
    run(repository, "git", "switch", "hiraku/task/smoke")
    run(repository, "git", "add", ".")
    run(repository, "git", "commit", "-m", "configure smoke task")
    hiraku(
        repository,
        "exp",
        "create",
        "smoke:baseline",
        "--entrypoint",
        "train --score 0.65",
    )
    hiraku(repository, "exp", "set", "smoke:baseline", "--commit", "HEAD")
    hiraku(
        repository,
        "launch",
        "--experiment",
        "baseline",
        "smoke:train",
        "--score",
        "0.65",
    )

    rows = json.loads(hiraku(repository, "runs", "smoke", "--json").stdout)
    assert len(rows) == 1
    assert rows[0]["params.seed"] == "1"
    assert rows[0]["metrics.score"] == 0.65


@pytest.mark.parametrize("example_name", ["smoke", "heart-disease"])
def test_example_tracking_adapter_uses_mlflow_location_from_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_name: str,
) -> None:
    tracking_db = tmp_path / example_name / "mlflow.db"
    tracking_db.parent.mkdir(parents=True)
    tracking_uri = sqlite_uri(tracking_db)
    experiment_name = f"{example_name}-runs"
    monkeypatch.setenv("HIRAKU_TRACKING_BACKEND", "mlflow")
    monkeypatch.setenv("HIRAKU_MLFLOW_TRACKING_URI", tracking_uri)
    monkeypatch.setenv("HIRAKU_MLFLOW_EXPERIMENT", experiment_name)
    monkeypatch.setenv("HIRAKU_TASK_ID", example_name)
    monkeypatch.setenv("HIRAKU_EXPERIMENT_ID", "trial")
    monkeypatch.setenv("HIRAKU_SOURCE_COMMIT", "a" * 40)
    monkeypatch.delenv("HIRAKU_TABLE_PATH", raising=False)

    module_path = PROJECT_ROOT / f"examples/{example_name}/example_tracking.py"
    spec = importlib.util.spec_from_file_location(
        f"{example_name.replace('-', '_')}_example_tracking", module_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    run_id = module.record_run(
        {"model": "test", "seed": 7},
        {"score": 0.75},
        run_name="backend-selection",
    )

    client = mlflow.tracking.MlflowClient(tracking_uri=tracking_uri)
    recorded = client.get_run(run_id)
    assert recorded.data.params["task_id"] == example_name
    assert recorded.data.params["experiment_id"] == "trial"
    assert recorded.data.params["commit"] == "a" * 40
    assert recorded.data.metrics["score"] == 0.75
    assert client.get_experiment(recorded.info.experiment_id).name == experiment_name
