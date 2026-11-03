import argparse
import os
import uuid
from datetime import UTC, datetime

import pandas as pd

from hiraku.config import HirakuConfig

FIELDS = [
    "id",
    "job_id",
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

if not os.environ.get("HIRAKU_JOB_ID"):
    raise RuntimeError("Run this example through Hiraku to record job attribution")
runs_file = HirakuConfig.discover().root / "runs.csv"
row = {
    "id": uuid.uuid4().hex,
    "job_id": os.environ["HIRAKU_JOB_ID"],
    "record_id": os.environ["HIRAKU_RECORD_ID"],
    "commit": os.environ.get("HIRAKU_COMMIT", ""),
    "git_state": os.environ.get("HIRAKU_GIT_STATE", "unavailable"),
    "timestamp": started.isoformat(),
    "duration": f"{(datetime.now(UTC) - started).total_seconds():.3f}",
    "param.seed": args.seed,
    "metric.val/accuracy": accuracy,
}
pd.DataFrame([row], columns=FIELDS).to_csv(
    runs_file,
    mode="a",
    header=not runs_file.exists() or runs_file.stat().st_size == 0,
    index=False,
)
