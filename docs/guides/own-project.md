# Use your own project

You need a Git repository and an evaluation script with its dependencies
declared in your project. Start with a command such as
`uv run python evaluate.py` that works independently of Hiraku.

## Install and initialize

From a local Hiraku source checkout:

```bash
uv tool install .
```

Then change to your research project's root:

```bash
hiraku init
```

Review `git status`, then commit your evaluation script, dependency files,
and project setup. If the project is new, initialize Git first. Hiraku does
not create the evaluation script or install its training dependencies.

## Describe and evaluate an approach

```bash
hiraku task create my-task \
  --goal "Improve validation accuracy" \
  --question "Does the new feature representation help?" \
  --set 'metrics=[{name: val/accuracy, direction: maximize}]'

hiraku experiment create baseline \
  --hypothesis "The existing representation establishes a reference." \
  --commit HEAD \
  --set 'entrypoints={evaluate: {command: [uv, run, python, evaluate.py]}}'

hiraku launch baseline:evaluate
hiraku run list --task my-task
```

An experiment entrypoint executes the command you supply. The metric
declaration does not calculate or log the metric: add logging to your script
using [CSV](tracking-csv.md), [W&B](tracking-wandb.md), or
[MLflow](tracking-mlflow.md) before expecting results in `run list`.

For the next approach, implement and commit the source changes, then create a
new experiment bound to that commit. Branches are optional; see
[Git and execution](../reference/git.md).

See the [CLI reference](../api/cli.md) for update, inspection, and branch options.
