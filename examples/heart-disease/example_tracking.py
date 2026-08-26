from __future__ import annotations

import csv
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def record_run(
    params: dict[str, Any],
    metrics: dict[str, float],
    *,
    run_name: str | None = None,
    table_metrics: dict[str, Any] | None = None,
) -> str:
    backend = os.environ["HIRAKU_TRACKING_BACKEND"]
    provenance = {
        "task_id": os.environ["HIRAKU_TASK_ID"],
        "experiment_id": os.environ["HIRAKU_EXPERIMENT_ID"],
        "commit": os.environ["HIRAKU_SOURCE_COMMIT"],
    }
    if backend == "table":
        path = Path(os.environ["HIRAKU_TABLE_PATH"])
        path.parent.mkdir(parents=True, exist_ok=True)
        run_id = run_name or uuid.uuid4().hex
        metric_values = table_metrics or metrics
        fields = [
            "run_id",
            "task_id",
            "experiment_id",
            "commit",
            "created_at",
            *params,
            *metrics,
        ]
        write_header = not path.exists() or path.stat().st_size == 0
        with path.open("a", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            if write_header:
                writer.writeheader()
            writer.writerow(
                {
                    "run_id": run_id,
                    **provenance,
                    "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    **params,
                    **metric_values,
                }
            )
        return run_id
    if backend == "mlflow":
        import mlflow

        mlflow.set_tracking_uri(os.environ["HIRAKU_MLFLOW_TRACKING_URI"])
        mlflow.set_experiment(os.environ["HIRAKU_MLFLOW_EXPERIMENT"])
        with mlflow.start_run(run_name=run_name) as run:
            mlflow.log_params({**provenance, **params})
            mlflow.log_metrics(metrics)
            mlflow.set_tag("hiraku.status", "finished")
            return run.info.run_id
    raise RuntimeError(f"Unsupported HIRAKU_TRACKING_BACKEND value: {backend!r}")
