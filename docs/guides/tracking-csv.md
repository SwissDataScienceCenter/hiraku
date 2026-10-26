# Track with CSV

CSV is the default backend. Records normally read `runs.csv` from the Hiraku
project root. Override this per record with
`tracking_backend: {type: csv, path: results/runs.csv}`; relative paths resolve
from the project root.

## Log from your script

The CSV adapter reads records; it does not write them. Your evaluation script
can append a row using this schema:

```csv
id,record_id,commit,git_state,timestamp,duration,entrypoint,metric.val/accuracy,param.seed
run-001,baseline,abc123,dirty,2026-01-01T12:00:00Z,42.5,evaluate,0.84,42
```

Choose a unique run ID for each execution. Set `timestamp` to when the run
started, taken before any work, and `duration` to its wall-clock length in
seconds; both are optional. The dashboard shows them as the run's start time
and duration. Prefix metric columns with
`metric.` and parameter columns with `param.`. Use `HIRAKU_GIT_STATE` to
distinguish a clean commit from a dirty working tree based on that commit.

During a launch, `HIRAKU_ROOT` points to the original project root. Use it so
worktree dispatches write to the persistent table rather than the temporary
checkout:

```python
import os
from pathlib import Path

runs_path = Path(os.environ["HIRAKU_ROOT"]) / "runs.csv"
```

Use `HIRAKU_RECORD_ID`, `HIRAKU_ENTRYPOINT`,
`HIRAKU_COMMIT`, and `HIRAKU_GIT_STATE` to populate attribution. See
Initialize the [Heart Disease example](../examples/heart-disease/README.md) with
`hiraku example create heart-disease` to get a complete writer in `train.py`.

## Inspect

```bash
hiraku runs --record my-task --all --sort val/accuracy
```

Metric values read from CSV remain strings in normalized runs; sorting
converts numeric values where possible. A script that fails before writing
a row has no recorded execution in this backend.
