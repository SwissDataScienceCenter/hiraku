# Contributing

Contributions are welcome. Pick a feature to add, create a feature branch and pull request. Share and discuss :)

We track planned features and ideas using [GitHub issues](https://github.com/SwissDataScienceCenter/hiraku/issues).


## Development setup

For your first use of Hiraku, start with [Your first experiment](first-experiment.md).
This page is for contributors working on the package.

### Install from source

```bash
git clone https://github.com/SwissDataScienceCenter/hiraku.git
cd hiraku
uv sync --extra dev
uv run hiraku --help
```

The development extra installs the test and documentation dependencies.
Add `--extra mlflow` or `--extra wandb` when working on those integrations.

### Check changes

From the Hiraku checkout:

```bash
uv run pytest
uv run ruff check tests
uv run zensical build --clean
```

Tests use temporary projects. Backend tests can substitute test clients;
running a live tracking example still requires the corresponding service
configuration.

### Preview documentation

```bash
uv run zensical serve
```

Command options come from `hiraku.cli:main` through mkdocs-click.
Python API pages come from docstrings through mkdocstrings. Keep guides focused
on complete workflows; avoid duplicating option lists or method signatures.
Example READMEs retain their runnable commands.

For contributor proposals, see [Research stories](stories/index.md).

See [Architecture](core.md) for core responsibilities.
