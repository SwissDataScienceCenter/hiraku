from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.errors import BackendError
from hiraku.record import Record, load_record
from hiraku.run import Run
from hiraku.tracking import CSVBackend


def test_csv_normalization_filtering_and_sorting(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    task = Record.create("task", config=config)
    Record.create("exp", parent_id=task.id, config=config)
    (config.root / "runs.csv").write_text(
        "id,record_id,commit,timestamp,entrypoint,url,metric.loss,param.seed,note\n"
        "later,exp,abc,2026-01-02T00:00:00Z,train,,1.5,2,kept\n"
        "earlier,exp,,2026-01-01T00:00:00+00:00,,,2.5,1,raw\n"
        "other,task,,,,,3.0,3,ignored\n"
    )
    runs = task.runs(config=config)
    assert [run.id for run in runs] == ["earlier", "later", "other"]
    assert runs[0].timestamp is not None and runs[0].timestamp.tzinfo is UTC
    assert runs[0].metrics == {"loss": "2.5"}
    assert runs[0].params == {"seed": "1"}
    assert runs[0].raw == {"note": "raw"}
    assert {
        run.id for run in load_record("exp", config=config).runs(config=config)
    } == {"earlier", "later", "other"}
    assert (
        task.runs(
            config=config,
            sort="loss",
            ascending=True,
            limit=1,
        )[0].id
        == "later"
    )


def test_run_queries_use_explicit_owner_filters_without_resolution(
    tmp_path: Path,
) -> None:
    config = HirakuConfig.init(tmp_path)
    task = Record.create("task", config=config)
    experiment = Record.create("exp", parent_id=task.id, config=config)

    class CapturingBackend:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def list_runs(self, *, record_id: str, **query: object) -> list[Run]:
            self.calls.append(record_id)
            return []

        def list_all_recorded_runs(self, **options: object) -> list[Run]:
            self.calls.append("source")
            return []

    backend = CapturingBackend()
    task.runs(backend=backend)
    task.path.unlink()
    experiment.runs(backend=backend)
    assert backend.calls == ["source", "source"]
    with pytest.raises(BackendError):
        CSVBackend(config).list_runs(record_id="")


def test_real_mlflow_sqlite_adapter(tmp_path: Path) -> None:
    mlflow = pytest.importorskip("mlflow")
    config = HirakuConfig.init(tmp_path)
    task = Record.create(
        "task",
        config=config,
        tracking_backend="mlflow",
        mlflow_experiment="hiraku-test",
    )
    Record.create("exp", parent_id=task.id, config=config)
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    mlflow.set_experiment("hiraku-test")
    with mlflow.start_run() as active:
        mlflow.set_tags(
            {
                "hiraku.record_id": "exp",
                "hiraku.commit": "a" * 40,
                "hiraku.entrypoint": "train",
                "unmapped": "kept",
            }
        )
        mlflow.log_param("seed", 7)
        mlflow.log_metric("loss", 2.0, step=0)
        mlflow.log_metric("loss", 1.0, step=1)
        run_id = active.info.run_id
    with mlflow.start_run() as active:
        mlflow.set_tag("unrelated", "no-record-id")
        mlflow.log_metric("loss", 4.0)
    run = task.runs(config=config)[0]
    assert len(task.runs(config=config)) == 1
    assert run.id == run_id
    assert run.record_id == "exp"
    assert run.commit == "a" * 40
    assert run.entrypoint == "train"
    assert run.params == {"seed": "7"}
    assert run.metrics["loss"] == 1.0
    assert run.timestamp is not None and run.timestamp.tzinfo is UTC
    assert run.raw["tags"]["unmapped"] == "kept"
    assert load_record("exp", config=config).runs(config=config)[0].id == run_id
    assert {item.id for item in task.runs(config=config)} == {run_id}
