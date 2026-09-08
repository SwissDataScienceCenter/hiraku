# Configuration reference

The project marker is `hiraku.yml`. The default configuration is:

```yaml
hiraku_dir: hiraku
default_dispatch: launch.sh
task_branch: hiraku/tasks/{task_id}
experiment_branch: hiraku/experiments/{task_id}/{experiment_id}
default_program: interactive
tracking_backend:
  type: csv
active_task_id: null
active_experiment_id: null
```

Hiraku searches upward from the current directory for `hiraku.yml`.
`hiraku_dir` resolves beneath the project root and contains `tasks/`,
`programs/`, `actions/`, `contexts/`, and `dispatch/`. Records use
Markdown with YAML front matter.

The active IDs are conveniences managed by the CLI; explicit task and
experiment arguments take precedence.

A task's `tracking_backend` overrides the project default. Use the
[CSV](../guides/tracking-csv.md), [W&B](../guides/tracking-wandb.md), or
[MLflow](../guides/tracking-mlflow.md) guide for logging and query configuration.

The [Python API](../api/config.md) documents `HirakuConfig`.
