# Digits

Train a small neural network on scikit-learn's Digits dataset and
compare weak and strong L2 regularization using MLflow learning curves.

The dataset contains 1,797 8×8 images. The example runs on CPU and logs training
and validation cross-entropy and accuracy after every epoch.

The network has one hidden layer of 64 neurons. Each `partial_fit` call performs
one epoch while preserving optimizer state. Both experiments use the same
stratified split, initialization seed, and 40-epoch budget. Pixels are divided
by their known maximum of 16.

## Setup

Install Hiraku with its MLflow extra in your CLI environment first. See
[installation options](https://swissdatasciencecenter.github.io/hiraku/installation/)
for prerequisites and other environments. Then create a new project:

```bash
uv tool install --force 'hiraku[mlflow]'
hiraku example create digits --git
cd digits
uv sync
git add .
git commit -m "initialize Digits example"
```

To browse the example without training, install the `dashboard` extra too and
create a project with `--with-results`. This copies the research records and a
prepared MLflow database containing six runs. Each regularization setting has
three runs of 20 epochs, one per seed (1, 2, and 3).

```bash
uv tool install --force 'hiraku[mlflow,dashboard]'
hiraku example create digits digits-dashboard --with-results
cd digits-dashboard
uv sync
```

Hiraku reads the prepared `mlflow.db` through an MLflow tracking server. Start
the server from the project directory in one terminal:

```bash
uv run mlflow server --backend-store-uri sqlite:///mlflow.db \
  --host 127.0.0.1 --port 5000
```

If you have the Hiraku repository checked out, you can also run `uv sync` and
start the server directly from `examples/digits/`, which already contains the
prepared records and database.

In another terminal, point Hiraku to it before querying runs or opening the
dashboard:

```bash
export MLFLOW_TRACKING_URI="http://127.0.0.1:5000"
hiraku dashboard --record digit-classification
```

Use `hiraku dashboard --init --edit --record digit-classification` to copy the
notebook into the project and edit it. Run details link to the MLflow UI when
the dashboard is configured with its URL. See the dashboard guide for details.

MLflow creates `mlflow.db` in the project root on the first launch. The training
script uses the Hiraku project root to keep that location stable. Set
`MLFLOW_TRACKING_URI` or `MLFLOW_EXPERIMENT_NAME` to override the training
defaults. Hiraku's MLflow backend requires `MLFLOW_TRACKING_URI` to point to an
HTTP(S) tracking server; see the MLflow guide when adapting the example to a
custom tracking store.

## Create the research root and child records

The example includes MLflow configuration in
`hiraku/leaderboards/default.yml`. The leaderboard reads results; the training
script configures its logging destination. Use `--with-results` to include the
prepared records and database as well.

```bash
hiraku record create digit-classification \
  --title "Digit classification" \
  --type task \
  --set "goal=Understand how L2 regularization affects Digits learning curves" \
  --set "question=Does stronger regularization reduce overfitting on the Digits dataset?"

hiraku record create digit-classification/weak-l2 \
  --title "Weak L2 regularization" \
  --parent digit-classification --type experiment \
  --set "summary=Weak L2 baseline" \
  --set "hypothesis=Weak regularization fits training data well but may overfit" \
  --commit HEAD

hiraku record create digit-classification/strong-l2 \
  --title "Strong L2 regularization" \
  --parent digit-classification --type experiment \
  --set "summary=Stronger L2 comparison" \
  --set "hypothesis=Stronger regularization reduces the training-validation gap" \
  --commit HEAD

hiraku record list

hiraku run --record digit-classification/weak-l2 -- uv run python train.py --alpha 0.0001 \
  --epochs 40 --hidden-units 64 --seed 42 --validation-fraction 0.25
hiraku run --record digit-classification/strong-l2 -- uv run python train.py --alpha 1.0 \
  --epochs 40 --hidden-units 64 --seed 42 --validation-fraction 0.25
```

## Compare final metrics and inspect curves

```bash
hiraku leaderboard runs

# list with additional details
hiraku leaderboard runs \
  --params alpha,epochs,seed \
  --metrics train/log_loss,val/log_loss,train/accuracy,val/accuracy \
  --sort val/log_loss --ascending

hiraku leaderboard runs --record digit-classification/strong-l2 --json

uv run mlflow ui --backend-store-uri sqlite:///mlflow.db --host 127.0.0.1 --port 5000
```

Open <http://127.0.0.1:5000>, select `hiraku-digits`, and compare the two runs
in the native MLflow interface.

One MLflow experiment contains both Hiraku child records. The script logs native
`hiraku.record_id` and `hiraku.job_id` tags for record and execution attribution.
Hiraku reads the latest value of each metric.  Full epoch histories remain in MLflow and its UI.

You can list the same runs using Hiraku:
```
hiraku leaderboard runs
```

Or browse them, with a per-run plot and links into the MLflow UI, in the
[dashboard](https://swissdatasciencecenter.github.io/hiraku/guides/dashboard/).
See that guide to add a UI link to a custom project. Then open the dashboard:

```bash
hiraku dashboard --record digit-classification
```

??? info "Example run results"
    The table is a compact, illustrative view of final MLflow values. Click a
    column heading to sort it; full epoch histories remain available in MLflow.

    | id | record | timestamp | commit | val/log_loss | val/accuracy |
    | --- | --- | --- | --- | ---: | ---: |
    | `15895756` | `weak-l2` | `2026-09-10 06:35:05Z` | `17f59bac` | 0.12493475669652965 | 0.9688888888888889 |
    | `f6f6de2f` | `strong-l2` | `2026-09-10 06:35:10Z` | `17f59bac` | 0.4801207515207299 | 0.9266666666666666 |
