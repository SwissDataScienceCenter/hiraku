# Iris with Weights & Biases

This example trains deterministic logistic-regression and random-forest
classifiers on scikit-learn's built-in Iris dataset and logs the results to
W&B. Hiraku reads the resulting W&B runs through its tracking adapter.

From a copy of this directory:

```bash
uv sync
export WANDB_API_KEY=your-api-key
export WANDB_ENTITY=your-entity       # optional for a personal project
export WANDB_PROJECT=hiraku-iris
export WANDB_GROUP=iris-baselines     # optional grouping label
export WANDB_TAGS=baseline,sklearn    # optional comma-separated tags

git init -b main
hiraku init
git add .
git commit -m "initialize Iris W&B example"
```

Create a research root configured with the W&B backend:

```bash
hiraku record create iris --type task \
  --set "goal=Maximize Iris test accuracy" \
  --set "question=How accurately can model families classify Iris?" \
  --set 'metrics=[{name: test/accuracy, direction: maximize}, {name: test/f1_macro, direction: maximize}, {name: test/log_loss, direction: minimize}]' \
  --set 'tracking_backend={type: wandb, project: hiraku-iris}'

hiraku record create logistic --parent iris --type experiment \
  --set "summary=Logistic regression baseline" \
  --set "hypothesis=A regularized linear classifier provides a strong Iris baseline." \
  --commit HEAD \
  --set 'entrypoints={train: {command: [uv, run, python, train.py], args: {model: logistic, seed: 42, test-size: 0.25}}}'

hiraku record create random-forest --parent iris --type experiment \
  --set "summary=Random forest comparison" \
  --set "hypothesis=A random forest improves Iris classification accuracy." \
  --commit HEAD \
  --set 'entrypoints={train: {command: [uv, run, python, train.py], args: {model: random_forest, seed: 42, test-size: 0.25}}}'

hiraku record list
```

Launch and inspect the run:

```bash
hiraku launch train --record logistic
hiraku launch train --record random-forest
hiraku runs --record iris --json
hiraku runs --record iris \
  --metrics test/accuracy,test/f1_macro,test/log_loss \
  --sort test/accuracy --descending
```

Each run stores Hiraku provenance, model and dataset configuration, seed,
Python version, runtime, tags, group, notes, and summary metrics. The training
script creates the remote W&B project on its first successful `wandb.init()`
call. Hiraku itself queries and normalizes W&B runs; it does not create the
W&B project or run.

??? info "Example run results"
    This small table mirrors the fields shown by the example’s W&B runs. Click
    a column heading to sort it; the values are illustrative.

    | Experiment | Run | Model | test/accuracy | test/f1_macro | Status |
    | --- | --- | --- | ---: | ---: | --- |
    | `logistic` | `iris-logistic-42` | Logistic regression | 0.9474 | 0.9468 | finished |
    | `random-forest` | `iris-rf-42` | Random forest | 0.9737 | 0.9735 | finished |
    | `random-forest` | `iris-rf-7` | Random forest | 0.9474 | 0.9469 | finished |
