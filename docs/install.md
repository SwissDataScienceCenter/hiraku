# Installation and Configuration

For an introduction to the workflow, see the [examples](examples.md),
[command-line reference](cli.md), [Python Core API](core.md), and the
[branching workflows](../README.md#linear-workflow-and-branching).

## Development installation

For local development:

```bash
git clone https://github.com/SwissDataScienceCenter/hiraku.git
cd hiraku
uv sync
uv run hiraku --help
```

Dashboard support and remote tracking integrations are optional. Install only
the corresponding extras needed by the checkout; they are not part of the core
runtime.

To install `hiraku` from a checkout onto `PATH` without activating its virtual
environment:

```bash
uv tool install .
hiraku --help
```

## Project initialization

Run initialization from the intended project root:

```bash
hiraku init
```

`hiraku init` creates the fixed project marker `hiraku.yml` and the default
metadata tree under `hiraku/`. It materializes editable copies of the bundled
programs, actions, and dispatch scripts, and installs the repository-local
`/hiraku` skill by default. Initialization does not create a task or experiment;
both active IDs start unset.

Initialization fails if `hiraku.yml` already exists. It does not merge with or
overwrite an existing Hiraku project.

After initialization, the default layout is:

```text
hiraku.yml
hiraku/
├── tasks/
├── programs/
├── actions/
├── contexts/
└── dispatch/
    └── launch.sh
```

## Project discovery

`hiraku.yml` is the fixed, non-configurable project marker. Hiraku starts at the
current directory, or a supplied path, and searches upward until it finds that
file. The directory containing it is the project root. The metadata paths
defined inside the file remain configurable.

## Configuration

A default `hiraku.yml` has the following shape:

```yaml
task_pattern: hiraku/tasks/{task_id}/task.{suffix}
experiment_pattern: hiraku/tasks/{task_id}/experiments/{experiment_id}.{suffix}
program_pattern: hiraku/programs/{program_id}/program.{suffix}
action_pattern: hiraku/actions/{entity}/{action_id}/action.{suffix}
context_pattern: hiraku/contexts/{context_id}/context.{suffix}
dispatch_dir: hiraku/dispatch
default_dispatch: launch.sh

task_branch: hiraku/tasks/{task_id}
experiment_branch: hiraku/experiment/{task_id}/{experiment_id}

default_program: interactive
tracking_backend: csv

active_task_id: null
active_experiment_id: null
```

`{suffix}` expands only to `md` and `yml`. A record cannot have both
representations: if both paths exist for one logical ID, Hiraku reports an
error. Markdown records contain YAML front matter and optional prose; YAML
records contain structured fields only.

The default tracking backend is CSV. MLflow and Weights & Biases are selectable
alternatives. A task-level `tracking_backend` field overrides the project
default for that task. Hiraku queries runs through the selected backend; model
code continues to log through the backend's native interface.

All configured relative paths are resolved against the project root and must
remain inside it. The `hiraku.yml` marker itself cannot be relocated through
configuration.
