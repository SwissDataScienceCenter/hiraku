# Digits classification with MLflow logging

Train a small neural network on scikit-learn's bundled Digits dataset (1,797
8×8 images), logging training and validation cross-entropy and accuracy after
every epoch. This CPU-only example compares weak and strong L2 regularization:
**Does stronger regularization reduce overfitting?** The outcome is a research
question, not a guaranteed improvement.

The network has one hidden layer of 64 neurons. Each `partial_fit` call performs
one epoch while preserving optimizer state. Both experiments use the same
stratified split, initialization seed, and 40-epoch budget. Pixels are divided
by their known maximum of 16; validation data is never used to fit preprocessing.
BLAS uses one thread to keep this small workload inexpensive.

## Setup

Install Hiraku with its MLflow extra in your CLI environment first (from the
Hiraku checkout: `uv pip install -e '.[mlflow]'`). Copy this example directory
outside the checkout, then run these commands from the copy:

```bash
uv sync
# Keep these exports in the shell used for both launches.
export MLFLOW_TRACKING_URI="sqlite:///$(pwd)/mlflow.db"
export MLFLOW_EXPERIMENT_NAME=hiraku-digits

git init -b main
hiraku init
git add .
git commit -m "initialize Digits MLflow example"
```

After dependencies are installed, training needs no network, account, server,
or GPU. MLflow creates its local database on the first launch. The absolute URI
is essential: committed launches run in temporary worktrees, but must write to
the original project's database. The training script explicitly requires these
MLflow variables as well as Hiraku's injected provenance.

## Create the task and experiments

```bash
hiraku task create digits \
  --goal "Understand how L2 regularization affects Digits learning curves" \
  --question "Does stronger regularization reduce overfitting?" \
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

hiraku launch weak-l2:train
hiraku launch strong-l2:train
```

## Compare final metrics and inspect curves

```bash
hiraku run list --task digits \
  --params alpha,epochs,seed \
  --metrics train/log_loss,val/log_loss,train/accuracy,val/accuracy \
  --sort val/log_loss --ascending

hiraku run list --task digits --experiment strong-l2 --json

uv run mlflow ui --backend-store-uri "$MLFLOW_TRACKING_URI" --host 127.0.0.1 --port 5000
```

Open <http://127.0.0.1:5000>, select `hiraku-digits`, and compare the two runs.
Plot the loss and accuracy metrics against **Step** (epochs 1–40). Examine the
training-validation gap, whether validation loss levels off or rises, and the
tradeoff between underfitting and overfitting. Repeat with additional seeds
before drawing a general conclusion; this validation split is not a final test
set.

One MLflow experiment contains both Hiraku experiments. Native MLflow tags
`hiraku.task_id`, `hiraku.experiment_id`, `hiraku.commit`, and `hiraku.entrypoint`
link each run back to Hiraku. The script owns logging; Hiraku queries the latest
value of each metric. Full epoch histories remain in MLflow and its UI.
