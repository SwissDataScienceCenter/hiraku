# Start from scratch

## Install Hiraku

From a local Hiraku source checkout, install the tool with the extras you want to use.

```bash
uv tool install .

# or with extra dependencies for MLflow and W&B
uv tool install ".[mlflow,wandb]"
```

## Initialize

If the project is new, initialize Git first. 

```bash
mkdir my-project
cd my-project
git init
```

Then initialize Hiraku in the project root. The `--example` option copies the `src/` tree of a packaged example into the project.

```bash
hiraku init --example minimal
git add .
git commit -m "initial commit"
```

Review `hiraku status` to confirm that the project is correctly initialized.

## Create a task, experiment, and log a run

The `minimal` example installs a small `evaluate.py` script that launches with
`uv run python evaluate.py --seed 0`. Replace it with your own evaluation when
you are ready.

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
