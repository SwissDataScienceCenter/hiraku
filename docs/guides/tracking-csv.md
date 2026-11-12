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
id,record_id,hiraku_job_id,timestamp,metric.val/accuracy,param.seed
run-001,baseline,JOB_ID,2026-01-01T12:00:00Z,0.84,42
```

Choose a unique run ID for each execution. Prefix metric columns with
`metric.` and parameter columns with `param.`. Populate `record_id` and
`hiraku_job_id` from `HIRAKU_RECORD_ID` and `HIRAKU_JOB_ID` respectively.
The optional `commit` column describes source-reported code provenance.

Your script chooses the CSV location, relative to its working directory:

```python
import os
from pathlib import Path

runs_path = Path("runs.csv")
```

See [local jobs](jobs.md) for attribution and persistence.
Initialize the [Heart Disease example](../examples/heart-disease/README.md) with
`hiraku example create heart-disease` to get a complete writer in `train.py`.

The `id` column is required and is the source run ID. The `record_id`
column is optional; use `hiraku leaderboard runs --include-unattributed` to include runs
without a record ID.

## Inspect

```bash
hiraku leaderboard runs --sort val/accuracy
```

Metric values read from CSV are normalized to floats. A script that fails before writing
a row has no recorded execution in this backend.
