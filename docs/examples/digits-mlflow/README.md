# Digits classification with MLflow logging

Train a small neural network on scikit-learn's bundled Digits dataset (1,797
8×8 images) and log training and validation cross-entropy and accuracy after
every epoch to MLflow. The example runs on CPU and compares weak and strong L2
regularization.

The network has one hidden layer of 64 neurons. Each `partial_fit` call performs
one epoch while preserving optimizer state. Both experiments use the same
stratified split, initialization seed, and 40-epoch budget. Pixels are divided
by their known maximum of 16.

## Setup

Install Hiraku with its MLflow extra in your CLI environment first (from the
Hiraku checkout: `uv tool install --force '.[mlflow]'`). Then create and
initialize a new project:

```bash
hiraku example create digits-mlflow --git
cd digits-mlflow
uv sync
git add .
git commit -m "initialize Digits MLflow example"
```

Alternatively, to browse the example without training, install the `dashboard`
extra too and initialize a separate project with the six bundled runs:

```bash
hiraku example create digits-mlflow digits-dashboard --with-results
cd digits-dashboard
hiraku dashboard --record digits
```

The dashboard reads the prepared `mlflow.db` directly, so an MLflow server is
not required. Use `hiraku dashboard --init --edit --record digits` to copy the
notebook into the project and edit it. Run details only show an MLflow link
after you start the MLflow UI. See the dashboard guide for adding a UI link to
a custom project.

MLflow creates `mlflow.db` in the project root on the first launch. The training
script uses the Hiraku project root to keep that location stable. Set
`MLFLOW_TRACKING_URI` or `MLFLOW_EXPERIMENT_NAME` only
when you want to override the local defaults. These variables affect the
training process only; see the MLflow guide when adapting the example to a
custom tracking store.

## Create the research root and child records

The example ships with MLflow configured as the project-wide tracking backend.

```bash
hiraku record create digits --type task \
  --set "goal=Understand how L2 regularization affects Digits learning curves" \
  --set "question=Does stronger regularization reduce overfitting on the Digits dataset?" \
  --set 'metrics=[{name: val/log_loss, direction: minimize}, {name: val/accuracy, direction: maximize}]'

hiraku record create weak-l2 --parent digits --type experiment \
  --set "summary=Weak L2 baseline" \
  --set "hypothesis=Weak regularization fits training data well but may overfit" \
  --commit HEAD

hiraku record create strong-l2 --parent digits --type experiment \
  --set "summary=Stronger L2 comparison" \
  --set "hypothesis=Stronger regularization reduces the training-validation gap" \
  --commit HEAD

hiraku record list

hiraku run --record weak-l2 -- uv run python train.py --alpha 0.0001 \
  --epochs 40 --hidden-units 64 --seed 42 --validation-fraction 0.25
hiraku run --record strong-l2 -- uv run python train.py --alpha 1.0 \
  --epochs 40 --hidden-units 64 --seed 42 --validation-fraction 0.25
```

## Compare final metrics and inspect curves

```bash
hiraku runs --record digits --all

# list with additional details
hiraku runs --record digits --all \
  --params alpha,epochs,seed \
  --metrics train/log_loss,val/log_loss,train/accuracy,val/accuracy \
  --sort val/log_loss --ascending

hiraku runs --record strong-l2 --json

uv run mlflow ui --backend-store-uri sqlite:///mlflow.db --host 127.0.0.1 --port 5000
```

Open <http://127.0.0.1:5000>, select `hiraku-digits`, and compare the two runs
in the native MLflow interface.

One MLflow experiment contains both Hiraku child records. Native MLflow tags
`hiraku.record_id` and `hiraku.commit`
link each run back to Hiraku. The script owns logging; Hiraku queries the latest
value of each metric. Full epoch histories remain in MLflow and its UI.

You can list the same runs using Hiraku:
```
hiraku runs --record digits --all
```

Or browse them, with a per-run plot and links into the MLflow UI, in the
[dashboard](../../guides/dashboard.md). See that guide to add a UI link to a
custom project. Then open the dashboard:

```bash
hiraku dashboard --record digits
```

??? info "Example run results"
    The table is a compact, illustrative view of final MLflow values. Click a
    column heading to sort it; full epoch histories remain available in MLflow.

    | id | record | timestamp | commit | val/log_loss | val/accuracy |
    | --- | --- | --- | --- | ---: | ---: |
    | `15895756` | `weak-l2` | `2026-09-10 06:35:05Z` | `17f59bac` | 0.12493475669652965 | 0.9688888888888889 |
    | `f6f6de2f` | `strong-l2` | `2026-09-10 06:35:10Z` | `17f59bac` | 0.4801207515207299 | 0.9266666666666666 |
