# Track with MLflow

MLflow can store runs in a local SQLite database or a remote server.

## Install and configure

From the Hiraku source checkout:

```bash
uv tool install --force '.[mlflow]'
```

In the training project:

```bash
uv add mlflow
hiraku record update my-task \
  --set 'tracking_backend={type: mlflow, database: mlflow.db, experiment_name: my-task}'
```

The record must already exist. The database path is relative to the Hiraku
project root. For a remote server, specify `tracking_uri` instead of
`database`; the options are mutually exclusive.

When `tracking_uri` is an HTTP(S) server without credentials, the dashboard
links each run directly to that server's UI:

```yaml
tracking_backend:
  type: mlflow
  tracking_uri: https://mlflow.example.com:5000
  experiment_name: my-task
```

For a local SQLite `database`, or a credential-bearing remote URI, no run link
is shown unless you set `ui_url` explicitly. `ui_url` must be an absolute
HTTP(S) URL with a host and no credentials, query, or fragment. It may include
a port and a deployment path prefix:

```yaml
tracking_backend:
  type: mlflow
  database: mlflow.db
  experiment_name: my-task
  ui_url: https://mlflow.example.com/deployments/main
```

Run links use MLflow's native run route
`/#/experiments/<experiment_id>/runs/<run_id>`. If the UI is not reachable
from the dashboard browser, Hiraku still shows the run's provenance and
metrics; the link is omitted only when no UI base can be resolved safely.

## Log from your script

Set an absolute URI pointing to that same database before launch:

```bash
export MLFLOW_TRACKING_URI="sqlite:///$PWD/mlflow.db"
```

Your script uses native MLflow logging and Hiraku attribution tags:

```python
import os
import mlflow

mlflow.set_experiment("my-task")
with mlflow.start_run():
    mlflow.set_tags({
        "hiraku.record_id": os.environ["HIRAKU_RECORD_ID"],
        "hiraku.entrypoint": os.environ["HIRAKU_ENTRYPOINT"],
    })
    if os.environ.get("HIRAKU_COMMIT"):
        mlflow.set_tag("hiraku.commit", os.environ["HIRAKU_COMMIT"])
    mlflow.log_metric("val/accuracy", accuracy)  # calculated by your code
```

The launcher inherits the environment; it does not derive the training
process's MLflow URI from the record's backend settings. An absolute URI keeps
worktree dispatches writing to the original database.

## Inspect

```bash
hiraku runs --record my-task --json
```

Hiraku reads native run IDs, parameters, metrics, and attribution tags.
The MLflow experiment name is a backend grouping, distinct from a Hiraku
record ID.
