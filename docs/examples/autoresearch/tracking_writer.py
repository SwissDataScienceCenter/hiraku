from __future__ import annotations

import csv
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

METRICS = ("val_bpb", "training_seconds", "peak_vram_mb", "mfu_percent")


def _provenance() -> dict[str, str]:
    required = (
        "HIRAKU_TASK_ID",
        "HIRAKU_EXPERIMENT_ID",
        "HIRAKU_COMMIT",
        "HIRAKU_GIT_STATE",
        "HIRAKU_ROOT",
    )
    missing = [name for name in required if name not in os.environ]
    if missing:
        raise RuntimeError(
            f"Missing launch environment variables: {', '.join(missing)}"
        )
    return {
        "task_id": os.environ["HIRAKU_TASK_ID"],
        "experiment_id": os.environ["HIRAKU_EXPERIMENT_ID"],
        "commit": os.environ["HIRAKU_COMMIT"],
        "git_state": os.environ["HIRAKU_GIT_STATE"],
        "entrypoint": os.environ.get("HIRAKU_ENTRYPOINT", ""),
    }


def record_run(metrics: dict[str, float], status: str) -> str:
    provenance = _provenance()
    run_id = uuid.uuid4().hex
    path = Path(os.environ["HIRAKU_ROOT"]) / "runs.csv"
    fields = [
        "id",
        "task_id",
        "experiment_id",
        "commit",
        "git_state",
        "timestamp",
        "entrypoint",
        "status",
        *(f"metric.{name}" for name in METRICS),
    ]
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        if write_header:
            writer.writeheader()
        writer.writerow(
            {
                "id": run_id,
                **provenance,
                "timestamp": datetime.now(UTC).isoformat(),
                "status": status,
                **{f"metric.{name}": metrics.get(name, "") for name in METRICS},
            }
        )
    return run_id
