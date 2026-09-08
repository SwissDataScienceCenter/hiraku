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

* A `Task` defines the research goal, question, metrics, entrypoints, and
tracking backend. It is stored as a Markdown record under `hiraku/tasks/`.
* An `Experiment` describes one implementation attempt and its hypothesis. It
belongs to a task and may refer to a committed Git source state. An experiment
without a commit is a proposal.
* A `Run` is one execution of an experiment entrypoint. It contains the source
commit when available, parameters, metrics, timestamps, and backend metadata.
Runs are read through the selected tracking backend.
* An entrypoint is an existing executable command. Hiraku constructs its argument
vector and invokes it through the project dispatcher. It does not create the
training or evaluation script.

> Git remains responsible for commits, branches, and source history. Hiraku can
bind an experiment to a commit and execute that commit in a temporary worktree.
Branches are optional for the core workflow.

> A proposal becomes runnable when it is bound to a commit. Later code changes
should be committed and represented by a new or updated experiment according
to the project workflow.

For more details, see [Essential concepts](concepts.md).

## Documentation

- [Guides](examples/minimal/README.md)
- [CLI reference](api/cli.md)
- [Python API](api/index.md)
- [Contributing](contributing.md)
