# Iris with Weights & Biases

This example trains deterministic logistic-regression and random-forest
classifiers on scikit-learn's built-in Iris dataset and logs the results to
W&B. Hiraku reads the resulting W&B runs through its tracking adapter.

## 1. Install Hiraku and initialize the example

From a local Hiraku source checkout, install the Hiraku CLI:

```bash
git clone https://github.com/SwissDataScienceCenter/hiraku.git
cd hiraku
uv tool install --force '.[wandb]'
```

Then create a project and let Hiraku install the training script, processed
dataset, and dependencies for this example.

```bash
hiraku example create iris-wandb --git
cd iris-wandb
uv sync
git add .
git add hiraku/dispatch
git commit -m "initialize Iris W&B example"
```

## 2. Login to W&B

```bash
uv run wandb login
```

The example uses the `hiraku-iris` project, model-specific groups, and
built-in tags by default.

Set `WANDB_ENTITY` only if you want to log under a specific team or account.

## 3. Define the research root

Create a research root configured with the W&B backend:

```bash
hiraku record create iris --type task \
  --set "goal=Maximize Iris test accuracy" \
  --set "question=How accurately can model families classify Iris?" \
  --set 'metrics=[{name: test/accuracy, direction: maximize}, {name: test/f1_macro, direction: maximize}, {name: test/log_loss, direction: minimize}]' \
  --set 'tracking_backend={type: wandb, project: hiraku-iris}'

hiraku record show iris
hiraku status
```

## 4. Create a baseline experiment

```bash
hiraku record create logistic --parent iris --type experiment \
  --set "summary=Logistic regression baseline" \
  --set "hypothesis=A regularized linear classifier provides a strong Iris baseline." \
  --commit HEAD

hiraku record show logistic
hiraku record show logistic

hiraku run --record logistic --backend local --dry-run -- \
  uv run python train.py --model logistic --seed 42 --test-size 0.25
job_id=$(hiraku run --record logistic --backend local -- \
  uv run python train.py --model logistic --seed 42 --test-size 0.25)
hiraku job wait "$job_id"
hiraku runs --record iris --all
hiraku runs --record logistic
```

## 5. Run a second experiment

```bash
hiraku record create random-forest --parent iris --type experiment \
  --set "summary=Random forest comparison" \
  --set "hypothesis=A random forest improves Iris classification accuracy." \
  --commit HEAD

job_id=$(hiraku run --record random-forest --backend local -- \
  uv run python train.py --model random_forest --seed 42 --test-size 0.25)
hiraku job wait "$job_id"
hiraku runs --record iris --all --sort test/accuracy
```

Each run stores Hiraku provenance, model and dataset configuration, seed,
Python version, runtime, tags, group, notes, and summary metrics. The training
script creates the remote W&B project on its first successful `wandb.init()`
call. Hiraku itself queries and normalizes W&B runs; it does not create the
W&B project or run.

??? info "Example run results"
    These results are from the example's W&B runs. Click a column heading to
    sort the table.

    | Experiment | Run | Model | test/accuracy | test/f1_macro | test/log_loss |
    | --- | --- | --- | ---: | ---: | ---: |
    | `logistic` | `kwh698ea` | Logistic regression | 0.9474 | 0.9487 | 0.1757 |
    | `random-forest` | `y5czffeu` | Random forest | 0.9211 | 0.9230 | 0.1789 |

### Job execution

Jobs require a clean Git working tree and capture the selected commit in an isolated snapshot. Ignored datasets and environments are not copied; configure access through an optional dispatcher. Submission returns a job ID; wait before querying runs. Use `hiraku job logs JOB_ID` for output. Arguments after `--` replace the complete default command.
