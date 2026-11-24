# First experiment

Before you begin, [install Hiraku](installation.md).

Evaluate a baseline twice with different seeds and look up both results under one experiment record. Make sure Git is configured with your name and email.

## 1. Initialize a project

```bash
hiraku init my-project --git
cd my-project
hiraku leaderboard create local --source csv --path runs.csv
git add .
git commit -m "initial commit"
```

Initialization creates `hiraku.yml`. The leaderboard command configures CSV tracking. Create an `evaluate.py` file with a small evaluation that appends its result to `runs.csv`:

```python
import argparse
import csv
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--seed", type=int, default=0)
args = parser.parse_args()

expected = [0, 1, 1, 0]
predicted = [int(value >= 0.5) for value in [0.1, 0.8, 0.7, 0.2]]
accuracy = sum(
    actual == prediction
    for actual, prediction in zip(expected, predicted, strict=True)
) / len(expected)

fields = [
    "id",
    "record_id",
    "hiraku_job_id",
    "timestamp",
    "duration",
    "param.seed",
    "metric.val/accuracy",
]
path = Path("runs.csv")
write_header = not path.exists() or path.stat().st_size == 0
with path.open("a", encoding="utf-8", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=fields)
    if write_header:
        writer.writeheader()
    writer.writerow(
        {
            "id": uuid.uuid4().hex,
            "record_id": os.environ["HIRAKU_RECORD_ID"],
            "hiraku_job_id": os.environ["HIRAKU_JOB_ID"],
            "timestamp": datetime.now(UTC).isoformat(),
            "duration": 0,
            "param.seed": args.seed,
            "metric.val/accuracy": accuracy,
        }
    )
```

Commit the evaluation code so the experiment can reference it.

```bash
git add evaluate.py
git commit -m "add baseline evaluation"
```

## 2. Record the research goal

```bash
hiraku record create binary-classification \
  --title "Binary classification" \
  --type task \
  --set "goal=Improve validation accuracy on my dataset"
```

This creates `hiraku/records/binary-classification.md`. Use it to keep the goal and summarize results across experiments.

## 3. Describe the baseline

```bash
hiraku record create binary-classification/threshold-baseline \
  --title "Threshold baseline" \
  --parent binary-classification --type experiment \
  --set "hypothesis=The existing representation establishes a reference." \
  --commit HEAD

hiraku record list
```

The experiment has its own record. `--parent` connects it to the task, and `--commit HEAD` records the implementation commit.

## 4. Run the evaluation twice

```bash
hiraku run --record binary-classification/threshold-baseline -- uv run python evaluate.py --seed 0
hiraku run --record binary-classification/threshold-baseline -- uv run python evaluate.py --seed 1
```

Everything after `--` is the command to execute. Hiraku passes the record and job IDs through environment variables. The evaluation script calculates a score and appends it to `runs.csv` along with those IDs and the seed.

## 5. Review the runs and record a conclusion

```bash
hiraku leaderboard runs --record binary-classification/threshold-baseline
hiraku record edit binary-classification/threshold-baseline
```

The leaderboard should show two runs with `param:seed` values of `0` and `1` and a `val/accuracy` score for each. Compare the scores and use the record’s Markdown body to note what you observed.

To use your own evaluation, replace the script and follow the [CSV logging guide](guides/tracking-csv.md). Commit the new code and create a new experiment with `--commit HEAD`.
