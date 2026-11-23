# Track with W&B

W&B owns the runs; Hiraku queries them using matching record IDs.

## Install and configure

Install Hiraku with the [`wandb` extra](../getting_started.md#optional-features). In the training project, install the SDK and authenticate:

```bash
uv add wandb
uv run wandb login
```

Set the leaderboard source and explicit W&B project name in `hiraku/leaderboards/wandb.yml`.
The entity is optional and can be resolved by the authenticated W&B account.
The CLI and training process can have separate Python environments; both need
W&B installed.

```yaml
source:
  type: wandb
  project: my-wandb-project
```

## Log from your script

```python
import os
import wandb

with wandb.init(
    config={
        "hiraku.record_id": os.environ["HIRAKU_RECORD_ID"],
        "hiraku.job_id": os.environ["HIRAKU_JOB_ID"],
        "seed": 42,
    },
) as run:
    run.log({"test/accuracy": accuracy})  # calculated by your evaluation code
```

W&B assigns the run ID. Configure the logging project in your training code
or with `WANDB_PROJECT`; Hiraku does not infer it from leaderboards. Include `hiraku.record_id` in the
W&B run configuration so Hiraku can attribute the run; `hiraku.commit` is
optional. A W&B group alone does not provide record attribution. The leaderboard source
selects which W&B project to query and does not initialize a W&B run.

## Inspect

After the online run finishes syncing:

```bash
hiraku leaderboard runs --json
```

The adapter queries W&B's remote API. Offline SDK runs need syncing before
remote inspection.

The [Digits MLflow example](../examples/digits-mlflow/README.md) shows a complete
training script and record-attributed runs with a different tracking backend.
