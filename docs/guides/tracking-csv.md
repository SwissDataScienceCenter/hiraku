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
id,job_id,record_id,commit,git_state,timestamp,metric.val/accuracy,param.seed
run-001,job-id,baseline,abc123,clean,2026-01-01T12:00:00Z,0.84,42
```

Choose a unique run ID for each execution. Prefix metric columns with
`metric.` and parameter columns with `param.`. For CSV writers, retrieve the
current job explicitly:

```python
from hiraku.config import HirakuConfig
from hiraku.jobs import current_job

job = current_job()
if job is None:
    raise RuntimeError("Run through Hiraku to associate this CSV row with a job")
project_root = HirakuConfig.discover().root
```

Use `job.id`, `job.record_id`, `job.commit`, and `job.git_state` to populate
the CSV columns. Write the tracking table under the project root (or the
configured CSV path):

```python
runs_path = project_root / "runs.csv"
```

See the [Heart Disease example](../examples/heart-disease/README.md) for a
complete writer. Initialize it with `hiraku example create heart-disease`.

The `id` column is the tracking backend's run ID.

## Inspect

```bash
hiraku runs --record my-task --all --sort val/accuracy
```

Metric values read from CSV remain strings in normalized runs; sorting
converts numeric values where possible. A script that fails before writing
a row has no recorded execution in this backend.
