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
```

For a local project, this shorthand is enough:

```yaml
tracking_backend: mlflow
```

Hiraku uses `mlflow.db` in the project root and an experiment named after the
project directory. `hiraku run` supplies `MLFLOW_TRACKING_URI` and
`MLFLOW_EXPERIMENT_NAME` to the training process, so standard MLflow logging
uses the same store Hiraku queries. Existing environment values are preserved.

For a remote server or a different local database, use a mapping. The database
path is relative to the Hiraku project root. Specify `tracking_uri` instead of
`database`; the options are mutually exclusive.

Runs read from an HTTP(S) `tracking_uri` link to that server's web UI. For a
local `database`, set `ui_url` to the address where you serve `mlflow ui`:

```yaml
tracking_backend:
  type: mlflow
  database: mlflow.db
  experiment_name: my-task
  ui_url: http://127.0.0.1:5000
```

`ui_url` must be an absolute HTTP(S) URL without credentials, query, or
fragment; it may include a path prefix. Tracking URIs that contain credentials
are never turned into links.

## Log from your script

`hiraku run` sets the tracking URI and experiment name from `hiraku/hiraku.yml`. For
commands run outside Hiraku, set the URI yourself:

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
    })
    if os.environ.get("HIRAKU_COMMIT"):
        mlflow.set_tag("hiraku.commit", os.environ["HIRAKU_COMMIT"])
    mlflow.log_metric("val/accuracy", accuracy)  # calculated by your code
```

Hiraku resolves `database` from the project root and supplies the resulting
absolute tracking URI to commands it runs. For commands run outside Hiraku, set
the URI yourself as shown above.

## Inspect

```bash
hiraku runs --record my-task --all --json
```

Hiraku reads native run IDs, parameters, metrics, and attribution tags.
The MLflow experiment name is a backend grouping, distinct from a Hiraku
record ID.

??? warning "MLflow local database"
    With `database` (or the default `mlflow.db`), queries require an existing file
    and fail before constructing the MLflow client if it is missing. Log runs with
    MLflow first to create the database. Explicit `tracking_uri` values use native
    MLflow behavior without this file check.

    This check does not guarantee read-only storage access: MLflow may initialize
    tables, migrate some existing schemas, or recreate the default experiment when
    opening an existing database. Other outdated schemas require an explicit
    `mlflow db upgrade`. These are MLflow limitations; the standard MLflow client
    does not provide a read-only connection mode.

    For MLflow projects, install Hiraku in the project environment to avoid
    version conflicts.
