# Installation

## Installation

Choose the level of detail you need: [Quickstart](docs/quickstart.md),
[linear tutorial](docs/tutorial.md), or
[advanced branching, reviews, and remote tracking](docs/advanced.md).

For local development, the intended workflow is:

```bash
git clone https://github.com/SwissDataScienceCenter/hiraku.git
cd hiraku
uv sync
uv run hiraku --help
```

Install optional dashboard support with `uv sync --extra dashboard`, or both dashboard and MLflow
with `uv sync --extra dashboard --extra mlflow`.

To install `hiraku` from a checkout onto `PATH` without activating its virtual environment:

```bash
uv tool install .
hiraku --help
```

## Configuration

`hiraku init` creates `.hiraku.yml`, `.hiraku/context.yml`, the task directory, the bundled `interactive` and `autoresearch` programs, and the repository-local Hiraku skill. It adds the local context file to `.gitignore`.

A table-backed project uses configuration shaped like:

```yaml
task_path: hiraku/tasks/<task-id>/task.[md|yml] 
experiment_path: hiraku/tasks/<task-id>/experiments/<experiment-id>.[md|yml]
context_path: hiraku/context/<context-id>.md
action_path: hiraku/actions/<entity>/<action-id>.md
program_path: hiraku/programs/<program-id>/program.[md|yml]
task_branch: hiraku/tasks/<task-id>
default_program: interactive
tracking_backend: table
```

  > Todo: it can also make sense to define the tracking backend at the task level. Another idea is to define defaults that can be overwritten at task or experimet level (e.g., branch, program, ...). Then we add `program` as an optional key to task, and experiments.

  > This can also be a place to configure default fields for listing experiments and tasks

All configured paths are repository-relative and must remain inside the project root. The table format is inferred from `.csv` or `.tsv`. 
