# Track with CSV

CSV is the default backend. Records normally read `runs.csv` from the Hiraku
project root. Override this per record with
`tracking_backend: {type: csv, path: results/runs.csv}`; relative paths resolve
from the project root.

## Log from your script

The CSV adapter reads records; it does not write them. Your evaluation script
can append a row using this schema:

```csv
id,record_id,commit,git_state,timestamp,job_id,metric.val/accuracy,param.seed
run-001,baseline,abc123,clean,2026-01-01T12:00:00Z,job-001,0.84,42
```

Choose a unique run ID for each execution. Prefix metric columns with
`metric.` and parameter columns with `param.`. Use `HIRAKU_GIT_STATE` and
`HIRAKU_SOURCE_DIGEST` to identify the selected source snapshot. Job submission
requires a clean Git working tree.

During a job, `HIRAKU_ROOT` points to the captured source tree and
`HIRAKU_TRACKING_ROOT` points to the persistent project directory. Write the
tracking table there so it remains queryable after the isolated job completes:

```python
import os
from pathlib import Path

runs_path = Path(os.environ["HIRAKU_TRACKING_ROOT"]) / "runs.csv"
```

Use `HIRAKU_RECORD_ID`, `HIRAKU_JOB_ID`, `HIRAKU_COMMIT`,
`HIRAKU_GIT_STATE`, and `HIRAKU_SOURCE_DIGEST` to populate attribution. See
Initialize the [Heart Disease example](../examples/heart-disease/README.md) with
`hiraku example create heart-disease` to get a complete writer in `train.py`.

## Inspect

```bash
hiraku runs --record my-task --all --sort val/accuracy
```

Metric values read from CSV remain strings in normalized runs; sorting
converts numeric values where possible. A script that fails before writing
a row has no recorded execution in this backend.
