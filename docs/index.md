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

## Documentation

- [Essential concepts](concepts.md)
- [Guides](examples/minimal/README.md)
- [CLI reference](api/cli.md)
- [Python API](api/index.md)
- [Contributing](contributing.md)
