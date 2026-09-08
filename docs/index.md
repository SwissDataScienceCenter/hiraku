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

Follow [Your first experiment](first-experiment.md) for a complete workflow
using the bundled Heart Disease example. It uses the CSV backend and does not
require an external account.

If you already have a project, continue with [Use your own project](guides/own-project.md).
If an AI agent is doing the implementation, read [Work with an AI agent](guides/agent.md).

## Documentation

- [Essential concepts](concepts.md)
- [Guides](guides/own-project.md)
- [CLI reference](api/cli.md)
- [Python API](api/index.md)
- [Contributing](contributing.md)
