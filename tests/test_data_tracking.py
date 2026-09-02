from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC
from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.data import Experiment, Run, Task, load_experiment
from hiraku.errors import BackendError, EntrypointNotFoundError, ValidationError
from hiraku.tracking import CSVBackend


def test_metrics_entrypoints_inheritance_and_guarded_delete(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    task = Task.create(
        "task",
        config=config,
        metrics=[{"name": "loss", "direction": "minimize"}],
        entrypoints={"train": {"command": ["python", "train.py"]}},
    )
    experiment = Experiment.create("exp", task=task, config=config)
    assert experiment.is_proposal
    assert experiment.metrics == task.metrics
    assert task.entrypoint("train")["command"] == ["python", "train.py"]
    with pytest.raises(EntrypointNotFoundError):
        experiment.entrypoint("train")
    with pytest.raises(ValidationError):
        task.delete(config=config)

    invalid = Task("invalid", metrics=[{"name": "loss", "direction": "sideways"}])
    with pytest.raises(ValidationError):
        invalid.validate()


def test_csv_normalization_filtering_and_sorting(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    task = Task.create("task", config=config)
    Experiment.create("exp", task=task, config=config)
    (task.path.parent / "runs.csv").write_text(
        "id,task_id,experiment_id,commit,timestamp,entrypoint,url,metric.loss,param.seed,note\n"
        "later,task,exp,abc,2026-01-02T00:00:00Z,train,,1.5,2,kept\n"
        "earlier,task,exp,,2026-01-01T00:00:00+00:00,,,2.5,1,raw\n"
        "other,task,other,,,,,3.0,3,ignored\n"
    )
    runs = task.runs(config=config)
    assert [run.id for run in runs] == ["earlier", "later", "other"]
    assert runs[0].timestamp is not None and runs[0].timestamp.tzinfo is UTC
    assert runs[0].metrics == {"loss": "2.5"}
    assert runs[0].params == {"seed": "1"}
    assert runs[0].raw == {"note": "raw"}
    assert [
        run.id
        for run in load_experiment("exp", task=task, config=config).runs(config=config)
    ] == [
        "earlier",
        "later",
    ]
    assert (
        task.runs(config=config, sort="loss", ascending=True, limit=1)[0].id == "later"
    )


def test_run_is_immutable_and_missing_csv_is_empty(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    task = Task.create("task", config=config)
    assert task.runs(config=config) == []
    run = Run(id="one", metrics={}, params={})
    with pytest.raises(FrozenInstanceError):
        run.id = "two"  # type: ignore[misc]


def test_run_queries_use_explicit_owner_filters_without_resolution(
    tmp_path: Path,
) -> None:
    config = HirakuConfig.init(tmp_path)
    task = Task.create("task", config=config)
    experiment = Experiment.create("exp", task=task, config=config)

    class CapturingBackend:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str | None]] = []

        def list_runs(
            self,
            *,
            task_id: str,
            experiment_id: str | None = None,
            **query: object,
        ) -> list[Run]:
            self.calls.append((task_id, experiment_id))
            return []

    backend = CapturingBackend()
    task.runs(backend=backend)
    task.path.unlink()
    experiment.runs(backend=backend)
    assert backend.calls == [("task", None), ("task", "exp")]

    with pytest.raises(BackendError):
        CSVBackend(config).list_runs(task_id="")


def test_real_mlflow_sqlite_adapter(tmp_path: Path) -> None:
    mlflow = pytest.importorskip("mlflow")
    config = HirakuConfig.init(tmp_path)
    task = Task.create(
        "task",
        config=config,
        tracking_backend="mlflow",
        mlflow_experiment="hiraku-test",
    )
    Experiment.create("exp", task=task, config=config)
    tracking_uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment("hiraku-test")
    with mlflow.start_run() as active:
        mlflow.set_tags(
            {
                "hiraku.task_id": "task",
                "hiraku.experiment_id": "exp",
                "hiraku.commit": "a" * 40,
                "hiraku.entrypoint": "train",
                "unmapped": "kept",
            }
        )
        mlflow.log_param("seed", 7)
        mlflow.log_metric("loss", 2.0, step=0)
        mlflow.log_metric("loss", 1.0, step=1)
        run_id = active.info.run_id

    runs = task.runs(config=config)
    assert len(runs) == 1
    run = runs[0]
    assert run.id == run_id
    assert run.task_id == "task"
    assert run.experiment_id == "exp"
    assert run.commit == "a" * 40
    assert run.entrypoint == "train"
    assert run.params == {"seed": "7"}
    assert run.metrics["loss"] == 1.0
    assert run.timestamp is not None and run.timestamp.tzinfo is UTC
    assert run.raw["tags"]["unmapped"] == "kept"
    assert (
        load_experiment("exp", task=task, config=config).runs(config=config)[0].id
        == run_id
    )
