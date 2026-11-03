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
from hiraku import track_run

with track_run(
    backend="wandb", project="my-project", config={"seed": 42}
) as run:
    run.log({"test/accuracy": accuracy})  # calculated by your evaluation code
```

W&B assigns the run ID. Hiraku's wrapper adds the current job ID and resolves
record and Git provenance from the saved job. Choose the W&B project as usual;
the project's `tracking_backend` setting selects Hiraku's query adapter and
does not initialize a W&B run.

## Inspect

After the online run finishes syncing:

```bash
hiraku runs --record iris --all --json
```

The adapter queries W&B's remote API. Offline SDK runs need syncing before
remote inspection; an offline setting is not a local-run reader.

The `examples/iris-wandb/` directory includes a complete training script,
README, and notebook with tags, groups, and provenance.
