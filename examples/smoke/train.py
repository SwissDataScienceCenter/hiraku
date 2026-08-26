from __future__ import annotations

import argparse
import csv
import os
from datetime import UTC, datetime
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--score", type=float, required=True)
    args = parser.parse_args()

    path = Path(os.environ["HIRAKU_TABLE_PATH"])
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "run_id",
        "task_id",
        "experiment_id",
        "commit",
        "created_at",
        "seed",
        "score",
    ]
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        if write_header:
            writer.writeheader()
        writer.writerow(
            {
                "run_id": f"seed-{args.seed}-score-{args.score:.2f}",
                "task_id": os.environ["HIRAKU_TASK_ID"],
                "experiment_id": os.environ["HIRAKU_EXPERIMENT_ID"],
                "commit": os.environ["HIRAKU_SOURCE_COMMIT"],
                "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "seed": args.seed,
                "score": f"{args.score:.6f}",
            }
        )


if __name__ == "__main__":
    main()
