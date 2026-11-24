# Installation

Use Python 3.11 or newer, Git, and `uv`. Install the CLI from the Hiraku repository and check that it is on your PATH.

```bash
uv tool install 'git+https://github.com/SwissDataScienceCenter/hiraku.git'
hiraku --help
```

If your shell cannot find `hiraku`, run `uv tool update-shell` and open a new terminal.

## Optional features

The first experiment uses CSV, which is included. Add `mlflow`, `wandb`, or `dashboard` to the install requirement for the features you use.

```bash
uv tool install 'hiraku[mlflow,dashboard] @ git+https://github.com/SwissDataScienceCenter/hiraku.git'
```

Use `--force` to replace an existing tool installation if `uv` reports that it is already installed.

## From a local checkout

From the root of a Hiraku checkout, run `uv tool install .` for the CLI or `uv sync --all-extras` to set up the development environment. In the development environment, run the CLI with `uv run hiraku`.

Next, [run your first experiment](first_experiment.md).
