# Start from scratch

## Install Hiraku

From a local Hiraku source checkout, install the tool with the extras you want to use.

```bash
uv tool install --force .

# or with extra dependencies for MLflow and W&B
uv tool install --force ".[mlflow,wandb]"
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
hiraku init . --example minimal
git add .
git commit -m "initial commit"
```

Review `hiraku status` to confirm that the project is correctly initialized.

## Create records and log a run

The `minimal` example installs a small `evaluate.py` script that launches with
`uv run python evaluate.py --seed 0`. Replace it with your own evaluation when
you are ready.

Create a research root and a child record.
```bash
hiraku record create my-task --type task \
  --set "goal=Improve validation accuracy on my dataset" \
  --set 'metrics=[{name: val/accuracy, direction: maximize}]'

hiraku record create baseline --parent my-task --type experiment \
  --set "hypothesis=The existing representation establishes a reference." \
  --commit HEAD \
  --set 'entrypoints={evaluate: {command: [uv, run, python, evaluate.py], args: {seed: 0}}}'

hiraku launch evaluate --record baseline
hiraku launch evaluate --record baseline -- --seed 1
hiraku record list
hiraku runs --record baseline
```

The entrypoint runs `uv run python evaluate.py --seed 0`. The script writes its
score and launch attribution to the project-root `runs.csv`, so the final
command lists the baseline result. Metric declarations describe how to compare
results; the evaluation script is responsible for calculating and logging.
For other tracking backends, see [W&B](../../guides/tracking-wandb.md) or
[MLflow](../../guides/tracking-mlflow.md).

For the next experiment, implement and commit the source changes, then create a
new experiment bound to that commit.
