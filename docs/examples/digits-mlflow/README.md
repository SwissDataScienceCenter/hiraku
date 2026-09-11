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
mkdir digits-mlflow
cd digits-mlflow
git init
hiraku init --example digits-mlflow
uv sync
git add .
git commit -m "initialize Digits MLflow example"
```

MLflow creates `mlflow.db` in the project root on the first launch. The training
script uses the Hiraku project root to keep that location stable for local and
worktree dispatchers. Set `MLFLOW_TRACKING_URI` or `MLFLOW_EXPERIMENT_NAME` only
when you want to override the local defaults.

## Create the task and experiments

```bash
hiraku task create digits \
  --goal "Understand how L2 regularization affects Digits learning curves" \
  --question "Does stronger regularization reduce overfitting on the Digits dataset?" \
  --set 'metrics=[{name: val/log_loss, direction: minimize}, {name: val/accuracy, direction: maximize}]' \
  --set 'tracking_backend={type: mlflow, database: mlflow.db, experiment_name: hiraku-digits}'

hiraku experiment create weak-l2 \
  --summary "Weak L2 baseline" \
  --hypothesis "Weak regularization fits training data well but may overfit" \
  --commit HEAD \
  --set 'entrypoints={train: {command: [uv, run, python, train.py], args: {alpha: 0.0001, epochs: 40, hidden-units: 64, seed: 42, validation-fraction: 0.25}}}'

hiraku experiment create strong-l2 \
  --summary "Stronger L2 comparison" \
  --hypothesis "Stronger regularization reduces the training-validation gap" \
  --commit HEAD \
  --set 'entrypoints={train: {command: [uv, run, python, train.py], args: {alpha: 1.0, epochs: 40, hidden-units: 64, seed: 42, validation-fraction: 0.25}}}'

hiraku experiment list

hiraku launch weak-l2:train
hiraku launch strong-l2:train
```

## Compare final metrics and inspect curves

```bash
hiraku run list 

# list with additional details
hiraku run list \
  --params alpha,epochs,seed \
  --metrics train/log_loss,val/log_loss,train/accuracy,val/accuracy \
  --sort val/log_loss --ascending

hiraku run list --task digits --experiment strong-l2 --json

uv run mlflow ui --backend-store-uri sqlite:///mlflow.db --host 127.0.0.1 --port 5000
```

Open <http://127.0.0.1:5000>, select `hiraku-digits`, and compare the two runs
in the native MLflow interface.

One MLflow experiment contains both Hiraku experiments. Native MLflow tags
`hiraku.task_id`, `hiraku.experiment_id`, `hiraku.commit`, and `hiraku.entrypoint`
link each run back to Hiraku. The script owns logging; Hiraku queries the latest
value of each metric. Full epoch histories remain in MLflow and its UI.

You can list the same runs using Hiraku:
```
hiraku run list
```

??? info "Example run results"
    The table is a compact, illustrative view of final MLflow values. Click a
    column heading to sort it; full epoch histories remain available in MLflow.

    | id | experiment | timestamp | commit | metric:val/log_loss | metric:val/accuracy |
    | --- | --- | --- | --- | ---: | ---: |
    | `15895756` | `weak-l2` | `2026-09-10 06:35:05Z` | `17f59bac` | 0.12493475669652965 | 0.9688888888888889 |
    | `f6f6de2f` | `strong-l2` | `2026-09-10 06:35:10Z` | `17f59bac` | 0.4801207515207299 | 0.9266666666666666 |
