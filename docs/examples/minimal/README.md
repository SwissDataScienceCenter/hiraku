# Use your own project

## Install Hiraku

From a local Hiraku source checkout, install the tool with the extras you want to use.

```bash
uv tool install .

# or with extra dependencies for MLflow and W&B
uv tool install ".[mlflow,wandb]"
```

## Initialize

If the project is new, initialize Git first. Inside your project directory, run:

```bash
hiraku init
```

Review `hiraku status` to confirm that the project is correctly initialized.

## Create a task, experiment, and log a run

For the example below, we assume that you have an evaluation script that launches with `uv run python evaluate.py --seed 0`. For a mock example, copy and commit [`docs/examples/minimal/evaluate.py`](evaluate.py).

Create a task and an experiment.
```bash
hiraku task create my-task \
  --goal "Improve validation accuracy on my dataset" \
  --set 'metrics=[{name: val/accuracy, direction: maximize}]'

hiraku experiment create baseline \
  --hypothesis "The existing representation establishes a reference." \
  --commit HEAD \
  --set 'entrypoints={evaluate: {command: [uv, run, python, evaluate.py], args: {seed: 0}}}'

hiraku launch baseline:evaluate
hiraku launch baseline:evaluate -- --seed 1


hiraku task list
hiraku experiment list
hiraku run list 
```

The entrypoint runs `uv run python evaluate.py --seed 0`. The script writes its
score and launch attribution to the project-root `runs.csv`, so the final
command lists the baseline result. Metric declarations describe how to compare
results; the evaluation script is responsible for calculating and logging. 
For other tracking backends, see [W&B](../../guides/tracking-wandb.md) or
[MLflow](../../guides/tracking-mlflow.md).

For the next experiment, implement and commit the source changes, then create a
new experiment bound to that commit.
