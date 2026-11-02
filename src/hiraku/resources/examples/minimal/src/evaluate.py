import argparse
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

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


def hiraku_config() -> dict[str, str]:
    """Return available Hiraku launch attribution for a CSV row."""
    if not os.environ.get("HIRAKU_RECORD_ID"):
        return {}
    return {
        field: value
        for field in ("record_id", "commit", "git_state")
        if (value := os.environ.get(f"HIRAKU_{field.upper()}"))
        and not (field == "git_state" and value == "unavailable")
    }


# Replace this tiny smoke check with your real evaluation.
expected = [0, 1, 1, 0]
predicted = [int(value >= 0.5) for value in [0.1, 0.8, 0.7, 0.2]]
accuracy = sum(a == b for a, b in zip(expected, predicted, strict=True)) / len(expected)

runs_file = Path(os.environ.get("HIRAKU_TRACKING_ROOT", ".")) / "runs.csv"
row = {
    "id": uuid.uuid4().hex,
    **hiraku_config(),
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
