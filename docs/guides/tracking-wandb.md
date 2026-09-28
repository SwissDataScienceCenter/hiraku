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
    project="hiraku-iris",
    group=os.environ["HIRAKU_RECORD_ID"],
    config={
        "hiraku.record_id": os.environ["HIRAKU_RECORD_ID"],
        "hiraku.commit": os.environ.get("HIRAKU_COMMIT"),
        "hiraku.entrypoint": os.environ["HIRAKU_ENTRYPOINT"],
    },
) as run:
    run.log({"test/accuracy": accuracy})  # calculated by your evaluation code
```

Use the same entity/project in logging and Hiraku configuration. Record settings
select the query backend; they do not initialize a W&B run or automatically
configure your training script.

## Inspect

After the online run finishes syncing:

```bash
hiraku runs --record iris --descendants --json
```

The adapter queries W&B's remote API. Offline SDK runs need syncing before
remote inspection; an offline setting is not a local-run reader.

The `examples/iris-wandb/` directory includes a complete training script,
README, and notebook with tags, groups, and provenance.
