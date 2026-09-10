from __future__ import annotations

from datetime import UTC
from pathlib import Path

from hiraku.config import HirakuConfig
from hiraku.data import Task
from hiraku.tracking.csv import CSVBackend, CSVBackendConfig


def test_csv_backend_config_is_validated() -> None:
    config = CSVBackendConfig.model_validate({"type": "csv", "path": "runs.csv"})
    assert config.path == Path("runs.csv")


def test_csv_backend_normalizes_runs_and_sorts(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    Task.create("iris", config=config)
    path = config.root / "runs.csv"
    path.write_text(
        "id,task_id,experiment_id,timestamp,metric.accuracy,param.seed,note\n"
        "a,iris,exp,2026-01-02T00:00:00Z,0.8,1,kept\n"
        "b,iris,exp,2026-01-01T00:00:00Z,0.9,2,raw\n",
        encoding="utf-8",
    )

    runs = CSVBackend(config).list_runs(
        task_id="iris", sort="accuracy", ascending=False
    )
    assert [run.id for run in runs] == ["b", "a"]
    assert runs[0].timestamp is not None and runs[0].timestamp.tzinfo is UTC
    assert runs[0].metrics == {"accuracy": "0.9"}
    assert runs[0].params == {"seed": "2"}
    assert runs[0].raw == {"note": "raw"}


def test_csv_backend_resolves_task_path_from_project_root(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    task = Task.create(
        "iris",
        config=config,
        tracking_backend={"type": "csv", "path": "results/iris.csv"},
    )
    path = tmp_path / "results" / "iris.csv"
    path.parent.mkdir()
    path.write_text("id,task_id\na,iris\n", encoding="utf-8")

    runs = task.runs(config=config)

    assert [run.id for run in runs] == ["a"]


def test_csv_backend_preserves_absolute_path_override(tmp_path: Path) -> None:
    project = tmp_path / "project"
    config = HirakuConfig.init(project)
    path = tmp_path / "external.csv"
    path.write_text("id,task_id\na,iris\n", encoding="utf-8")

    runs = CSVBackend(config, path=path).list_runs(task_id="iris")

    assert [run.id for run in runs] == ["a"]
