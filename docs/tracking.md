# Leaderboards

A leaderboard selects one CSV, MLflow, or W&B source and optional comparison
metrics. Definitions live in `hiraku/leaderboards/<name>.yml`; relative source
paths resolve from the project root.

```bash
hiraku leaderboard create validation --source mlflow --database mlflow.db \
  --experiment-name classification
hiraku leaderboard edit validation
```

Edit the YAML to select metric order and optional optimization directions:

```yaml
source:
  type: mlflow
  database: mlflow.db
  experiment_name: classification
metrics:
  - name: accuracy
    direction: maximize
  - name: loss
    direction: minimize
```

Omit `metrics` to discover them from runs; `metrics: []` displays none by default.
The CLI table shows run IDs and metrics by default. Add parameters with
`--params seed,model`; metadata and all parameters remain available in `--json`.
Missing metrics appear as empty values;
additional metrics remain available in returned runs. Metric and parameter names
are separate, so the same name can occur in both. Sorting uses plain metric
names and their declared direction, defaulting to ascending. Sorting applies only
to metrics. Without `sort`, runs are ordered by timestamp. Missing values sort
last; plots skip runs without the required finite values and report the count.

```bash
hiraku leaderboard list
hiraku leaderboard show validation --json
hiraku leaderboard runs --leaderboard validation --sort accuracy
hiraku leaderboard runs --leaderboard validation --include-unattributed
hiraku leaderboard runs --leaderboard validation --record baseline
hiraku dashboard --leaderboard validation
hiraku leaderboard delete validation
```

A single leaderboard is selected automatically; select a name when several exist.
Queries list runs with a nonempty record ID, even when the record file is missing.
Use `--include-unattributed` to include all source runs. `--record` matches an exact
record ID, independently of the active record and the include flag.


See [CSV](guides/tracking-csv.md), [MLflow](guides/tracking-mlflow.md),
[W&B](guides/tracking-wandb.md), and the [Python API](api/python/tracking.md).
CSV headers use `metric.` and `param.` prefixes; leaderboard metric names are plain.

## Project-specific Python readers

For existing results that need custom parsing or aggregation, create
`hiraku/leaderboards/reference.py` instead of YAML:

```python
from hiraku.tracking import Metric, Run

metrics = [Metric(name="accuracy", direction="maximize")]


def load_runs(project) -> list[Run]:
    # Read files relative to project.root; return normalized runs without writing.
    return [Run(id="baseline", metrics={"accuracy": 0.84})]
```

`metrics` is optional and follows the same rules as YAML. The function must return
a list of `Run` objects with unique IDs. Existing attribution, metric sorting,
limits, CLI, MCP and dashboard behavior applies. Runs without `record_id` require
`--include-unattributed` or the dashboard's corresponding checkbox.

Python definitions are trusted executable project code, like Python workflows.
Listing names never imports them; selecting/loading a definition executes its
module, and each query reloads its reader. Keep imports free of side effects and
readers read-only. Changes are picked up when querying or refreshing; no converted
tracking file is required. Exceptions identify the definition that failed.

Use `hiraku leaderboard show reference`, `edit reference`, and `delete reference`
with either format. A `.py` and `.yml` file cannot share a leaderboard name.
Python files are authored directly; `create` continues to produce YAML.
Alternatively, YAML can select a Python reader while retaining its own metrics:

```yaml
source:
  type: python
  path: analysis/read_results.py
metrics:
  - name: accuracy
    direction: maximize
```

Relative reader paths resolve from the project root. Optional module-level
`metrics` apply only when the definition itself is discovered as a `.py` file.
