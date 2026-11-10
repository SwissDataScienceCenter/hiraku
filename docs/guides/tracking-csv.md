# Track with CSV

The CSV source requires an explicit file path.
Create `hiraku/leaderboards/local.yml` to configure a leaderboard:

```yaml
source:
  type: csv
  path: results/runs.csv
```

Relative paths resolve from the project root.

Paths can reference environment variables using `${NAME}`:

```yaml
source:
  type: csv
  path: ${MY_RESULTS_DIR}/runs.csv
```

Define `MY_RESULTS_DIR` in the environment running Hiraku. Variables expand when
the CSV adapter is constructed; unset variables raise an error. Relative expanded
paths resolve from the project root, while absolute paths can refer to external
storage. The YAML retains the expression so each system can use its own location.
Expansion evaluates no shell commands or expressions.

For a project-local file, use `path: runs.csv`. From the CLI:

```bash
hiraku leaderboard create local --source csv --path '${MY_RESULTS_DIR}/runs.csv'
```

Single quotes preserve the reference for Hiraku to expand when querying runs.

## Log from your script

The CSV adapter reads records; it does not write them. Your evaluation script
can append a row using this schema:

```csv
id,record_id,commit,git_state,timestamp,metric.val/accuracy,param.seed
run-001,baseline,abc123,clean,2026-01-01T12:00:00Z,0.84,42
```

Choose a unique run ID for each execution. Prefix metric columns with
`metric.` and parameter columns with `param.`. Use `HIRAKU_GIT_STATE` to record
whether the current working tree had changes.

During execution, `HIRAKU_ROOT` and `HIRAKU_TRACKING_ROOT` point to the project
directory. Write the tracking table there:

```python
import os
from pathlib import Path

runs_path = Path(os.environ["HIRAKU_TRACKING_ROOT"]) / "runs.csv"
```

Use `HIRAKU_RECORD_ID`, `HIRAKU_COMMIT`, and
`HIRAKU_GIT_STATE` to populate attribution. See
Initialize the [Heart Disease example](../examples/heart-disease/README.md) with
`hiraku example create heart-disease` to get a complete writer in `train.py`.

The `id` column is required and is the source run ID. The `record_id`
column is optional; use `hiraku runs --include-unattributed` to include runs
without a record ID.

## Inspect

```bash
hiraku runs --sort val/accuracy
```

Metric values read from CSV are normalized to floats. A script that fails before writing
a row has no recorded execution in this backend.
