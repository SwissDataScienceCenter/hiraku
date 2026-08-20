from __future__ import annotations

import argparse
import csv
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

FIELDS = ["experiment_id", "commit", "run_id", "timestamp_utc", "variant", "score"]
SCORES = {"baseline": "0.50", "candidate": "0.75"}


def append_row(path: Path, row: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    if exists:
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.reader(stream)
            header = next(reader, None)
        if header != FIELDS:
            raise RuntimeError(f"Existing run table has an incompatible header: {header}")
    with path.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=sorted(SCORES), required=True)
    args = parser.parse_args()
    append_row(
        Path(os.environ["HIRAKU_TABLE_PATH"]),
        {
            "experiment_id": os.environ["HIRAKU_EXPERIMENT_ID"],
            "commit": os.environ["HIRAKU_GIT_COMMIT"],
            "run_id": str(uuid4()),
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "variant": args.variant,
            "score": SCORES[args.variant],
        },
    )


if __name__ == "__main__":
    main()
