# Track with CSV

CSV is the default backend. It reads `runs.csv` from the Hiraku project root.
To choose another CSV path, set the project-wide backend in `hiraku.yml`:

```yaml
tracking_backend:
  type: csv
  path: results/runs.csv
```

Relative paths resolve from the project root.

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

Use `HIRAKU_RECORD_ID`, `HIRAKU_COMMIT`, and `HIRAKU_GIT_STATE` to populate
attribution. The [Heart Disease example](../examples/heart-disease/README.md)
includes a complete CSV writer in `train.py`.

The `id` column is the tracking backend's run ID.

## Inspect

```bash
hiraku runs --record my-task --all --sort val/accuracy
```

Metric values read from CSV remain strings in normalized runs; sorting
converts numeric values where possible. A script that fails before writing
a row has no recorded execution in this backend.
