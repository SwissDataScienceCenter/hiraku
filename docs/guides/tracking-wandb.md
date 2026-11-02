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

Set the project-wide backend in `hiraku.yml`. With the `wandb` shorthand,
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

config = {"seed": 42}
record_id = os.environ.get("HIRAKU_RECORD_ID")
if record_id:
    config["hiraku.record_id"] = record_id
    git_state = os.environ.get("HIRAKU_GIT_STATE")
    if git_state and git_state != "unavailable":
        config["hiraku.git_state"] = git_state

with wandb.init(
    config=config,
) as run:
    run.log({"test/accuracy": accuracy})  # calculated by your evaluation code
```

W&B assigns the run ID. Hiraku supplies `WANDB_PROJECT` from the project
configuration unless it is already set. The project setting selects the query
backend and does not initialize a W&B run.
Runs without a record ID are not returned by Hiraku's attributed-run queries.

## Inspect

After the online run finishes syncing:

```bash
hiraku runs --record iris --all --json
```

The adapter queries W&B's remote API. Offline SDK runs need syncing before
remote inspection; an offline setting is not a local-run reader.

The `examples/iris-wandb/` directory includes a complete training script,
README, and notebook with tags, groups, and provenance.
