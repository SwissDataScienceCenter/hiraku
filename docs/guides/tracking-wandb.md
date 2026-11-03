# Track with W&B

W&B owns the runs; Hiraku queries them using matching record IDs.

## Install and configure

From your Hiraku source checkout:

```bash
uv tool install --force '.[wandb]'
```

In the training project, authenticate using the W&B CLI:

```bash
uv add wandb
uv run wandb login
```

Set the project-wide backend in `hiraku.yml` as follows:

```yaml
tracking_backend: wandb
```

## Log from your script

When you run your training script, use the `track_run` context manager to log metrics and artifacts. Specify your `project` W&B project name and additional `config` values logged with the run.

```python
from hiraku import track_run

with track_run(
    backend="wandb", project="my-project", config={"seed": 42}
) as run:
    run.log({"test/accuracy": accuracy})  # calculated by your evaluation code
```

## Inspect

After the online run finishes syncing:

```bash
hiraku runs --record <RECORD_ID> --all
```

The adapter queries W&B's remote API and fetches the run data.
