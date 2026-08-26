from __future__ import annotations

import csv
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path


def record_run(metrics: dict[str, float], status: str) -> str:
    run_id = uuid.uuid4().hex
    provenance = {
        "task_id": os.environ["HIRAKU_TASK_ID"],
        "experiment_id": os.environ["HIRAKU_EXPERIMENT_ID"],
        "commit": os.environ["HIRAKU_SOURCE_COMMIT"],
    }
    backend = os.environ["HIRAKU_TRACKING_BACKEND"]
    if backend == "table":
        path = Path(os.environ["HIRAKU_TABLE_PATH"])
        path.parent.mkdir(parents=True, exist_ok=True)
        fields = [
            "run_id",
            "task_id",
            "experiment_id",
            "commit",
            "created_at",
            "status",
            *metrics,
        ]
        write_header = not path.exists()
        with path.open("a", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            if write_header:
                writer.writeheader()
            writer.writerow(
                {
                    "run_id": run_id,
                    **provenance,
                    "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "status": status,
                    **metrics,
                }
            )
        return run_id
    if backend == "mlflow":
        import mlflow

        mlflow.set_tracking_uri(os.environ["HIRAKU_MLFLOW_TRACKING_URI"])
        mlflow.set_experiment(os.environ["HIRAKU_MLFLOW_EXPERIMENT"])
        with mlflow.start_run(run_name=run_id) as run:
            mlflow.log_params(provenance)
            mlflow.log_metrics(metrics)
            mlflow.set_tag("hiraku.status", status)
            return run.info.run_id
    raise RuntimeError(f"Unsupported Hiraku tracking backend: {backend}")
