# Track with MLflow

Hiraku queries an MLflow tracking server over HTTP(S). Run the server in the
environment that owns your MLflow installation and tracking data.

## Install and configure

Install Hiraku's client extra from the source checkout:

```bash
uv tool install --force '.[mlflow]'
```

In the training project, install MLflow and start its server:

```bash
uv add mlflow
uv run mlflow server --backend-store-uri sqlite:///mlflow.db \
  --host 127.0.0.1 --port 5000
```

Keep the server running. It owns database initialization and schema management.
Hiraku does not start it or manage its storage.

In another terminal, share the server and experiment settings between training
and Hiraku:

```bash
export MLFLOW_TRACKING_URI="http://127.0.0.1:5000"
export MLFLOW_EXPERIMENT_NAME="my-task"
```

Create `hiraku/leaderboards/local.yml`:

```yaml
source:
  type: mlflow
```

Alternatively, configure the server explicitly:

```yaml
source:
  type: mlflow
  tracking_uri: https://mlflow.example.org
  experiment_name: my-task
```

An explicit `tracking_uri` overrides `MLFLOW_TRACKING_URI`. One must be set to
an absolute HTTP(S) server URL without a query or fragment. Hiraku uses the
MLflow SDK's authentication settings, including `MLFLOW_TRACKING_TOKEN` and
`MLFLOW_TRACKING_USERNAME` / `MLFLOW_TRACKING_PASSWORD`.

An explicit `experiment_name` overrides both experiment environment variables.
Otherwise, Hiraku uses `MLFLOW_EXPERIMENT_NAME` or `MLFLOW_EXPERIMENT_ID`; when
both are set, they must identify the same experiment. Without either, the
experiment name defaults to the project directory name. An unknown experiment
name returns no runs instead of creating an experiment.

Runs link to the tracking server's web UI. Set `ui_url` when the UI is served at
a different address. It must be an absolute HTTP(S) URL without credentials,
query, or fragment and may include a path prefix. Tracking URLs containing
credentials are never turned into links.

## Log from your script

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

Add a `hiraku.commit` tag with the commit that the script runs at (for example
`git rev-parse HEAD`) if you use the research workflow: it only uses runs whose
commit is the experiment's.

## Inspect

```bash
hiraku leaderboard runs --json
```

Hiraku reads native run IDs, parameters, metrics, and attribution tags.
The MLflow experiment name is a backend grouping, distinct from a Hiraku
record ID.
