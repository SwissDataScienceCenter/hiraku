# Installation

Hiraku requires Python 3.11 or later and [uv](https://docs.astral.sh/uv/getting-started/installation/). 
To install Hiraku and verify the installation, run:

```bash
uv tool install hiraku
hiraku --version
hiraku --help
```

The `uv tool install` command installs Hiraku in a dedicated, isolated environment outside your project's `.venv`. This is a user-wide installation that makes the `hiraku` command available from any directory, regardless of the active project environment.

If your shell cannot find `hiraku`, run `uv tool update-shell` and open a new terminal.

To install Hiraku as a project dependency instead, see [Install inside a project environment](#install-hiraku-inside-a-project-environment).

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

## Install Hiraku inside a project environment

In some cases, you may want to install Hiraku inside a project environment. This allows you to integrate workflows, leaderboards and dashboards with your project dependencies. You can use either uv or pip to install `hiraku` as a project dependency.

### uv
With uv, prefix the documentation's `hiraku` commands with `uv run`. 

```bash
uv add 'hiraku[mlflow,dashboard]'
uv run hiraku --help
```

### Pip
With pip, activate your project's virtual environment before installing and running Hiraku.
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