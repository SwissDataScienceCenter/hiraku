# Track with CSV

CSV is the default backend. Each task normally reads
`hiraku/tasks/<task-id>/runs.csv`.

## Log from your script

The CSV adapter reads records; it does not write them. Your evaluation script
can append a row using this schema:

```csv
id,task_id,experiment_id,commit,timestamp,entrypoint,metric.val/accuracy,param.seed
run-001,my-task,baseline,,2026-01-01T12:00:00Z,evaluate,0.84,42
```

Choose a unique run ID for each execution. Prefix metric columns with
`metric.` and parameter columns with `param.`. Leave commit empty for an
uncommitted execution; do not invent a source hash.

During an attributed launch, `HIRAKU_TASK_FILE` points to the original
task record. Resolve the output beside it so committed launches write to the
same table:

```python
import os
from pathlib import Path

runs_path = Path(os.environ["HIRAKU_TASK_FILE"]).with_name("runs.csv")
```

Use `HIRAKU_TASK_ID`, `HIRAKU_EXPERIMENT_ID`, `HIRAKU_ENTRYPOINT`,
and `HIRAKU_COMMIT` when available to populate attribution. See
`examples/heart-disease/train.py` for a complete writer.

## Inspect

```bash
hiraku run list --task my-task --sort val/accuracy --descending
```

Metric values read from CSV remain strings in normalized runs; sorting
converts numeric values where possible. A script that fails before writing
a row has no recorded execution in this backend.
