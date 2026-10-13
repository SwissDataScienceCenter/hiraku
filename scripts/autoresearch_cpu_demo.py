"""Log simulated Autoresearch metrics for the portable dashboard demo."""

from __future__ import annotations

import argparse
import csv
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--val-bpb", type=float, default=1.0)
    parser.add_argument("--peak-vram-mb", type=float, default=1024.0)
    args = parser.parse_args()
    metrics = {
        "val_bpb": args.val_bpb,
        "training_seconds": 0.01,
        "peak_vram_mb": args.peak_vram_mb,
        "mfu_percent": 0.0,
    }
    row = {
        "id": uuid.uuid4().hex,
        "record_id": os.environ["HIRAKU_RECORD_ID"],
        "commit": os.environ["HIRAKU_COMMIT"],
        "git_state": os.environ["HIRAKU_GIT_STATE"],
        "timestamp": datetime.now(UTC).isoformat(),
        "entrypoint": os.environ["HIRAKU_ENTRYPOINT"],
        "status": "success",
        **{f"metric.{key}": value for key, value in metrics.items()},
    }
    runs_file = Path(os.environ["HIRAKU_ROOT"]) / "runs.csv"
    write_header = not runs_file.exists() or runs_file.stat().st_size == 0
    with runs_file.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row))
        if write_header:
            writer.writeheader()
        writer.writerow(row)
    print("Simulated CPU demo metrics (no model training or GPU allocation):")
    for key, value in metrics.items():
        print(f"{key}: {value:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
