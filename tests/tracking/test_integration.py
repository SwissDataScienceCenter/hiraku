from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.errors import BackendError
from hiraku.record import Record, load_record
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
    backend = task.resolve_tracking_backend(config=config)
    runs = backend.list_runs()
    assert [run.id for run in runs] == ["earlier", "later", "other"]
    assert [run.id for run in backend.list_runs(record_id="task")] == ["other"]
    assert runs[0].timestamp is not None and runs[0].timestamp.tzinfo is UTC
    assert runs[0].metrics == {"loss": "2.5"}
    assert runs[0].params == {"seed": "1"}
    assert runs[0].raw == {"note": "raw"}
    assert {run.id for run in backend.list_runs(record_id="exp")} == {
        "earlier",
        "later",
    }
    assert backend.list_runs(sort="loss", ascending=True, limit=1)[0].id == "later"


def test_backend_runs_rejects_empty_record_id_before_reading_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = HirakuConfig.init(tmp_path)
    backend = CSVBackend(config)
    monkeypatch.setattr(
        backend,
        "_read_source_runs",
        lambda **options: pytest.fail("tracking source should not be read"),
    )
    with pytest.raises(BackendError):
        backend.list_runs(record_id="")


def test_real_mlflow_sqlite_adapter(tmp_path: Path) -> None:
    mlflow = pytest.importorskip("mlflow")
    config = HirakuConfig.init(tmp_path)
    config.tracking_backend = {"type": "mlflow", "experiment_name": "hiraku-test"}
    task = Record.create("task", config=config)
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
    backend = task.resolve_tracking_backend(config=config)
    run = backend.list_runs()[0]
    assert len(backend.list_runs()) == 1
    assert backend.list_runs(record_id="task") == []
    assert run.id == run_id
    assert run.record_id == "exp"
    assert run.commit == "a" * 40
    assert run.raw["tags"]["hiraku.entrypoint"] == "train"
    assert run.params == {"seed": "7"}
    assert run.metrics["loss"] == 1.0
    assert run.timestamp is not None and run.timestamp.tzinfo is UTC
    assert run.raw["tags"]["unmapped"] == "kept"
    assert load_record("exp", config=config).runs(config=config)[0].id == run_id
