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

For a local project, create `hiraku/leaderboards/local.yml`:

```yaml
source:
  type: mlflow
```

Share MLflow's standard environment variables between training and Hiraku:

```bash
export MLFLOW_TRACKING_URI="sqlite:///$PWD/mlflow.db"
export MLFLOW_EXPERIMENT_NAME="my-task"
```

Explicit leaderboard `tracking_uri` or `database` overrides the environment URI.
An explicit `experiment_name` overrides both experiment environment variables.
Otherwise, Hiraku uses `MLFLOW_EXPERIMENT_NAME` or `MLFLOW_EXPERIMENT_ID`; when
both are set, they must identify the same experiment. An unknown experiment name
returns no runs instead of creating an experiment.

Without environment settings, Hiraku retains its defaults: `mlflow.db` in the
project root and an experiment named after the project directory. Leaderboards
only configure reading; training must inherit the same environment or explicitly
select the same destination and experiment.

For a remote server or a different local database, use a mapping. The database
path is relative to the Hiraku project root. Specify `tracking_uri` instead of
`database`; the options are mutually exclusive.

Runs read from an HTTP(S) `tracking_uri` link to that server's web UI. For a
local `database`, set `ui_url` to the address where you serve `mlflow ui`:

```yaml
source:
  type: mlflow
  database: mlflow.db
  experiment_name: my-task
  ui_url: http://127.0.0.1:5000
```

`ui_url` must be an absolute HTTP(S) URL without credentials, query, or
fragment; it may include a path prefix. Tracking URIs that contain credentials
are never turned into links.

## Log from your script

Set the logging URI in your script or environment, including for `hiraku job run`:

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
        "hiraku.job_id": os.environ["HIRAKU_JOB_ID"],
    })
    mlflow.log_metric("val/accuracy", accuracy)  # calculated by your code
```

## Inspect

```bash
hiraku leaderboard runs --json
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
