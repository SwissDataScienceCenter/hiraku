# Installation

Use Python 3.11 or newer and [uv](https://docs.astral.sh/uv/getting-started/installation/). To install Hiraku, run:

```bash
uv tool install hiraku
hiraku --version
hiraku --help
```

If your shell cannot find `hiraku`, run `uv tool update-shell` and open a new terminal.

## Optional features

Add `mlflow`, `wandb`, or `dashboard` as optional dependencies for the features you use.

```bash
uv tool install 'hiraku[mlflow,dashboard]'
```

When adding extras to an existing tool installation, use `--force` and include
all the extras you want to keep, for example:

```bash
uv tool install --force 'hiraku[mlflow,dashboard]'
```

## Install inside a project

You can also install Hiraku inside a project environment. With uv, prefix the
documentation's `hiraku` commands with `uv run`. With pip, activate your
project's virtual environment before installing and running Hiraku.

### uv

```bash
uv add 'hiraku[mlflow,dashboard]'
uv run hiraku --help
```

### Pip

```bash
python -m pip install 'hiraku[mlflow,dashboard]'
hiraku --help
```

## From a local checkout

Clone the repository and install the CLI from its root:

```bash
git clone https://github.com/SwissDataScienceCenter/hiraku.git
cd hiraku
uv tool install .
```

Use `uv tool install '.[mlflow,dashboard]'` to include optional features.
Add `--force` when reinstalling after changing code without changing the version.

For development, use `uv sync --all-extras` instead and run the CLI with
`uv run hiraku`. See the [contributor guide](https://github.com/SwissDataScienceCenter/hiraku/blob/main/CONTRIBUTING.md).

Next, [run your first experiment](first_experiment.md).
