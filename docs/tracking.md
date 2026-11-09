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
Parameters are discovered automatically. Missing metrics appear as empty values;
additional metrics remain available in returned runs. Metric and parameter names
are separate, so the same name can occur in both. Sorting uses plain metric
names and their declared direction, defaulting to ascending. Sorting applies only
to metrics. Without `sort`, runs are ordered by timestamp. Missing values sort
last; plots skip runs without the required finite values and report the count.

```bash
hiraku leaderboard list
hiraku leaderboard show validation --json
hiraku runs --leaderboard validation --sort accuracy
hiraku runs --leaderboard validation --include-unattributed
hiraku runs --leaderboard validation --record baseline
hiraku dashboard --leaderboard validation
hiraku leaderboard delete validation
```

A sole leaderboard is selected automatically; select a name when several exist.
Queries list runs with a nonempty record ID, even when the record file is missing.
Use `--include-unattributed` to include all source runs. `--record` matches an exact
record ID, independently of the active record and the include flag. Attribution
filters apply before sorting and limits. In Python and MCP, pass
`include_unattributed=True` to include unattributed runs.
The dashboard offers an **Include unattributed runs** toggle and record subtree
filtering; **Clear record filter** clears the subtree selection.
The collaborative workflow accepts a `leaderboard` argument for result queries.

Creation refuses to overwrite definitions. `edit` uses `$EDITOR` (default `vim`)
and validates after editing; invalid edits remain on disk for correction.
Listing returns names without reading definitions; `show NAME` loads and validates
one definition. Deletion removes only
the definition. API keys are excluded from serialization; configure credentials
through the backend environment.

Records describe research objectives and conclusions. Training code configures
logging destinations and records measurements; `hiraku run` supplies record and
commit attribution. Leaderboards query the results.

See [CSV](guides/tracking-csv.md), [MLflow](guides/tracking-mlflow.md),
[W&B](guides/tracking-wandb.md), and the [Python API](api/python/tracking.md).
CSV headers use `metric.` and `param.` prefixes; leaderboard metric names are plain.
