from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pytest

from hiraku.errors import BackendError
from hiraku.project import Project
from hiraku.tracking import CSVBackend, MLflowBackend, MLflowBackendConfig


def test_backend_runs_rejects_empty_record_id_before_reading_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Project.create(tmp_path)
    backend = CSVBackend(config, path="runs.csv")
    monkeypatch.setattr(
        backend,
        "_read_source_runs",
        lambda **options: pytest.fail("tracking source should not be read"),
    )
    with pytest.raises(BackendError):
        backend.list_runs(record_id="")


def test_real_mlflow_sqlite_adapter(tmp_path: Path) -> None:
    mlflow = pytest.importorskip("mlflow", exc_type=ImportError)
    config = Project.create(tmp_path)
    source = MLflowBackendConfig(experiment_name="hiraku-test")
    task = config.vault.create("task/task", type="task", title="task")
    config.vault.create(
        f"{task.id.rpartition('/')[0]}/exp/exp",
        type="task",
        parent_id=task.id,
        title="exp",
    )
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    mlflow.set_experiment("hiraku-test")
    with mlflow.start_run() as active:
        mlflow.set_tags(
            {
                "hiraku.record_id": "task/exp/exp",
                "hiraku.commit": "a" * 40,
                "hiraku.job_id": "job-1",
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
    backend = MLflowBackend(config, settings=source)
    run = backend.list_runs()[0]
    assert len(backend.list_runs()) == 1
    assert len(backend.list_runs(include_unattributed=True)) == 2
    assert backend.list_runs(record_id="task/task") == []
    assert run.id == run_id
    assert run.record_id == "task/exp/exp"
    assert run.commit == "a" * 40
    assert run.job_id == "job-1"
    assert run.params == {"seed": "7"}
    assert run.metrics["loss"] == 1.0
    assert run.timestamp is not None and run.timestamp.tzinfo is UTC
    assert run.raw["tags"]["unmapped"] == "kept"
    assert backend.list_runs(record_id="task/exp/exp")[0].id == run_id
