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
```

Set the project-wide backend and explicit W&B project name in `hiraku.yml`.
The entity is optional and can be resolved by the authenticated W&B account.
The CLI and training process can have separate Python environments; both need
W&B installed.

```yaml
tracking_backend:
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
        "hiraku.commit": os.environ.get("HIRAKU_COMMIT", ""),
        "seed": 42,
    },
) as run:
    run.log({"test/accuracy": accuracy})  # calculated by your evaluation code
```

W&B assigns the run ID. Hiraku supplies `WANDB_PROJECT` from the project
configuration unless it is already set. Include `hiraku.record_id` in the
W&B run configuration so Hiraku can attribute the run; `hiraku.commit` is
optional. A W&B group alone does not provide record attribution. The project
setting selects the query backend and does not initialize a W&B run.

## Inspect

After the online run finishes syncing:

```bash
hiraku runs --record iris --all --json
```

The adapter queries W&B's remote API. Offline SDK runs need syncing before
remote inspection; an offline setting is not a local-run reader.

The `examples/iris-wandb/` directory includes a complete training script,
README, and notebook with tags, groups, and provenance.
