# Leaderboards

Use the MLflow backend Python API to set a tag on a run by its server-side
run ID. For example, set the tag Hiraku uses for record attribution.

```python
from hiraku.tracking import MLflowBackend

backend = MLflowBackend(project, experiment_name="validation")
backend.set_tag(run_id, "hiraku.record_id", "experiments/baseline")
```

The backend creates its client and resolves the experiment during construction.
Tag writes address the run ID directly on the configured server.

A leaderboard's `source` tells Hiraku where to read runs. Set `source.type` to
`csv`, `mlflow`, or `wandb` and supply the settings for that backend. Configure
where runs are logged in your training script. A leaderboard can also select
metrics to compare. Definitions live in `hiraku/leaderboards/<name>.yml`;
relative source paths resolve from the project root.

```bash
hiraku leaderboard create validation --source mlflow \
  --tracking-uri http://127.0.0.1:5000 \
  --experiment-name classification
hiraku leaderboard edit validation
```

Edit the YAML to select metric order and optional optimization directions:

```yaml
source:
  type: mlflow
  tracking_uri: http://127.0.0.1:5000
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
hiraku leaderboard runs validation --sort accuracy
hiraku leaderboard runs validation --include-unattributed
hiraku leaderboard runs validation --record baseline
hiraku dashboard serve --leaderboard validation
hiraku leaderboard delete validation
```

A single leaderboard is selected automatically; select a name when several exist.
Queries list runs with a nonempty record ID, even when the record file is missing.
Use `--include-unattributed` to include all source runs. `--record` matches an exact
record ID, independently of the include flag.


See [CSV](tracking-csv.md), [MLflow](tracking-mlflow.md),
[W&B](tracking-wandb.md), and the [Python API](../api/python/leaderboards.md).
CSV headers use `metric.` and `param.` prefixes; leaderboard metric names are plain.
