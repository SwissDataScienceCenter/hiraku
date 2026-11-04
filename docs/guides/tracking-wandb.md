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

Set the project-wide backend in `hiraku/hiraku.yml`. With the `wandb` shorthand,
Hiraku uses the project directory name as the W&B project and leaves the entity
unset, allowing the authenticated W&B account to resolve it. The CLI and
training process can have separate Python environments; both need W&B installed.

The equivalent configuration value in YAML is:

```yaml
tracking_backend: wandb
```

## Log from your script

```python
import os
import wandb

with wandb.init(
    group=os.environ["HIRAKU_RECORD_ID"],
    config={
        "seed": 42,
    },
) as run:
    run.log({"test/accuracy": accuracy})  # calculated by your evaluation code
```

W&B assigns the run ID. Hiraku supplies `WANDB_PROJECT` from the project
configuration unless it is already set. Your script only needs its ordinary
`wandb.init()` call; there is no Hiraku-specific
W&B config to add. The project setting selects the query backend and does not
initialize a W&B run.

## Inspect

After the online run finishes syncing:

```bash
hiraku runs --record iris --all --json
```

The adapter queries W&B's remote API. Offline SDK runs need syncing before
remote inspection; an offline setting is not a local-run reader.

The `examples/iris-wandb/` directory includes a complete training script,
README, and notebook with tags, groups, and provenance.
