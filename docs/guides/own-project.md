# Use your own project

All you need is a git repository.

## Install and initialize

From a local Hiraku source checkout, install the tool with the extras you want to use.

```bash
uv tool install ".[mlflow,wandb]"
```

Then change to your research project's root:

```bash
hiraku init
```

Review `git status`, then commit your evaluation script, dependency files,
and project setup. If the project is new, initialize Git first. Hiraku does
not create the evaluation script or install its training dependencies.

## Describe and evaluate an approach

Create `evaluate.py` in the project root. This dependency-free smoke evaluation
reads the attribution that Hiraku adds to the launch environment and appends one
result to the task's `runs.csv` file:

```python
import argparse
import csv
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path


FIELDS = [
    "id",
    "task_id",
    "experiment_id",
    "commit",
    "timestamp",
    "entrypoint",
    "param.seed",
    "metric.val/accuracy",
]


parser = argparse.ArgumentParser()
parser.add_argument("--seed", type=int, default=0)
args = parser.parse_args()

# Replace this tiny smoke check with your real evaluation.
expected = [0, 1, 1, 0]
predicted = [int(value >= 0.5) for value in [0.1, 0.8, 0.7, 0.2]]
accuracy = sum(a == b for a, b in zip(expected, predicted)) / len(expected)

task_file = Path(os.environ["HIRAKU_TASK_FILE"])
runs_file = task_file.with_name("runs.csv")
write_header = not runs_file.exists() or runs_file.stat().st_size == 0

with runs_file.open("a", encoding="utf-8", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=FIELDS)
    if write_header:
        writer.writeheader()
    writer.writerow(
        {
            "id": uuid.uuid4().hex,
            "task_id": os.environ["HIRAKU_TASK_ID"],
            "experiment_id": os.environ.get("HIRAKU_EXPERIMENT_ID", ""),
            "commit": os.environ.get("HIRAKU_COMMIT", ""),
            "timestamp": datetime.now(UTC).isoformat(),
            "entrypoint": os.environ.get("HIRAKU_ENTRYPOINT", ""),
            "param.seed": args.seed,
            "metric.val/accuracy": accuracy,
        }
    )
```

Commit `evaluate.py`, then create and launch the baseline:

```bash
hiraku task create my-task \
  --goal "Improve validation accuracy on my dataset" \
  --set 'metrics=[{name: val/accuracy, direction: maximize}]'

hiraku experiment create baseline \
  --hypothesis "The existing representation establishes a reference." \
  --commit HEAD \
  --set 'entrypoints={evaluate: {command: [uv, run, python, evaluate.py], args: {seed: 0}}}'

hiraku launch baseline:evaluate
hiraku launch baseline:evaluate --seed 1


hiraku task list
hiraku experiment list
hiraku run list 
```

The entrypoint runs `uv run python evaluate.py --seed 0`. The script writes its
score and launch attribution to `hiraku/tasks/my-task/runs.csv`, so the final
command lists the baseline result. Metric declarations describe how to compare
results; the evaluation script is still responsible for calculating and logging
them. For other tracking backends, see [W&B](tracking-wandb.md) or
[MLflow](tracking-mlflow.md).

For the next experiment, implement and commit the source changes, then create a
new experiment bound to that commit.