import argparse
import csv
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

FIELDS = [
    "id",
    "record_id",
    "commit",
    "git_state",
    "timestamp",
    "duration",
    "param.seed",
    "metric.val/accuracy",
]


started = datetime.now(UTC)
parser = argparse.ArgumentParser()
parser.add_argument("--seed", type=int, default=0)
args = parser.parse_args()

# Replace this tiny smoke check with your real evaluation.
expected = [0, 1, 1, 0]
predicted = [int(value >= 0.5) for value in [0.1, 0.8, 0.7, 0.2]]
accuracy = sum(a == b for a, b in zip(expected, predicted, strict=True)) / len(expected)

runs_file = Path(os.environ["HIRAKU_TRACKING_ROOT"]) / "runs.csv"
write_header = not runs_file.exists() or runs_file.stat().st_size == 0

with runs_file.open("a", encoding="utf-8", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=FIELDS)
    if write_header:
        writer.writeheader()
    writer.writerow(
        {
            "id": uuid.uuid4().hex,
            "record_id": os.environ["HIRAKU_RECORD_ID"],
            "commit": os.environ.get("HIRAKU_COMMIT", ""),
            "git_state": os.environ["HIRAKU_GIT_STATE"],
            "timestamp": started.isoformat(),
            "duration": f"{(datetime.now(UTC) - started).total_seconds():.3f}",
            "param.seed": args.seed,
            "metric.val/accuracy": accuracy,
        }
    )
