# Hiraku

Hiraku provides a small, scriptable workflow for AI-assisted data-science
research. Define a task, record experiments, launch reproducible entrypoints,
and inspect tracked runs.


## Installation

```bash
uv tool install ".[mlflow,wandb]"
hiraku --help

# development setup
uv sync --all-extras
uv run hiraku --help
```

## Start here

Follow the [heart disease example](examples/heart-disease/README.md) for a complete workflow
using the bundled Heart Disease example. It uses the CSV backend and does not
require an external account.

## High-level overview

Hiraku aims to record a small research loop with the following three concepts:

```text
Task → Experiment → Run
```

* A `Task` defines the research task. It includes user-defined fields (e.g., a goal, question and metrics), documents the research progress, and specifies shared entrypoints and a tracking backend. Tasks are stored as  Markdown records under `hiraku/tasks/{task-id}/task.md`. 
* An `Experiment` describes one small, reproducible unit within a task. It links a code commit, experiment entrypoints, and runs. Experiments are stored as Markdown records under `hiraku/tasks/{task-id}/experiments/{experiment-id}.md`.
* A `Run` records an execution of an experiment. Runs are user managed and directly logged to CSV, MLflow, W&B, or any other backend. They are stored in the backend's own format and queried by Hiraku. 
* An `Entrypoint` is an existing executable command associated to task or an experiment with documented arguments. Hiraku provides launch helpers and customizable dispatch scripts.

> Git remains responsible for commits, branches, and source history. 
Branches are optional for the core workflow.


For more details, see [Essential concepts](concepts.md).

## Documentation

- [Guides](examples/guides/)
- [Examples](examples/)
- [CLI reference](api/cli.md)
- [Python API](api/index.md)
- [Contributing](contributing.md)
