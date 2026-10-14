# Track with MLflow

MLflow can store runs in a local SQLite database or a remote server.

## Install and configure

Install Hiraku and MLflow in the training project's environment (see
[Install Hiraku](installation.md)). Hiraku then reads the database with the
same MLflow version that your training code writes it with:

```bash
uv add --dev git+https://github.com/SwissDataScienceCenter/hiraku --branch main
uv add mlflow
uv run hiraku record update my-task \
  --set 'tracking_backend={type: mlflow, database: mlflow.db, experiment_name: my-task}'
```

The record must already exist. The database path is relative to the Hiraku
project root. For a remote server, specify `tracking_uri` instead of
`database`; the options are mutually exclusive.

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
uv run hiraku runs --record my-task --all --json
```

Hiraku reads native run IDs, parameters, metrics, and attribution tags.
The MLflow experiment name is a backend grouping, distinct from a Hiraku
record ID.
