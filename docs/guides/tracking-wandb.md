# Track with W&B

W&B owns the runs; Hiraku queries them using matching record IDs.

## Install and configure

From your Hiraku source checkout, install the CLI with the optional adapter:

```bash
uv tool install --force '.[wandb]'
```

In the training project, install the SDK and authenticate:

```bash
uv add wandb
uv run wandb login
hiraku record update iris \
  --set 'tracking_backend={type: wandb, entity: your-team, project: hiraku-iris}'
```

Replace the entity/project with your workspace. The record must already exist.
The CLI and training process can have separate Python environments; both need
W&B installed.

## Log from your script

```python
import os
import wandb

with wandb.init(
    entity="your-team",
    group=os.environ["HIRAKU_RECORD_ID"],
    config={
        "seed": 42,
    },
) as run:
    run.log({"test/accuracy": accuracy})  # calculated by your evaluation code
```

Use the same entity/project in logging and Hiraku configuration. When started
with `hiraku run`, Hiraku sets `WANDB_RUN_ID` to the invocation ID. Hiraku also
supplies the W&B project from the selected
record's `tracking_backend` configuration unless `WANDB_PROJECT` is already
set. Your script only needs its ordinary `wandb.init()` call; there is no
Hiraku-specific W&B config to add. Record settings select the query backend
and do not initialize a W&B run.

## Inspect

After the online run finishes syncing:

```bash
hiraku runs --record iris --all --json
```

The adapter queries W&B's remote API. Offline SDK runs need syncing before
remote inspection; an offline setting is not a local-run reader.

The `examples/iris-wandb/` directory includes a complete training script,
README, and notebook with tags, groups, and provenance.
