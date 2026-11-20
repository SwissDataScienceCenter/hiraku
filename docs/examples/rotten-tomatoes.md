# Rotten Tomatoes sentiment classification

Compare a logistic regression baseline with a random forest on the
[Rotten Tomatoes movie review dataset](https://huggingface.co/datasets/cornell-movie-review-data/rotten_tomatoes).
The example uses TF-IDF features and logs validation metrics to MLflow. Its
default leaderboard reads those MLflow runs.

## Create the project

```bash
hiraku example create rotten-tomatoes rotten-tomatoes --git
cd rotten-tomatoes
uv sync
git add .
git commit -m "initialize Rotten Tomatoes example"
```

The first training run downloads the dataset from Hugging Face. Runs are stored
in the project-local `mlflow.db` database by default. Set `MLFLOW_TRACKING_URI`
or `MLFLOW_EXPERIMENT_NAME` to select a remote tracking server or another
experiment name. The script serializes first-time database initialization, so
multiple runs can start together.

## Train and compare models

Create a task record and a logistic regression experiment:

```bash
hiraku record create sentiment-classification \
  --title "Sentiment classification" \
  --type task \
  --set "goal=Compare text classifiers on Rotten Tomatoes reviews"

hiraku record create sentiment-classification/logistic-regression \
  --title "Logistic regression baseline" \
  --parent sentiment-classification \
  --type experiment \
  --commit HEAD

hiraku run --record sentiment-classification/logistic-regression -- \
  uv run python train.py --model logistic-regression --seed 0
hiraku run --record sentiment-classification/logistic-regression -- \
  uv run python train.py --model logistic-regression --seed 1
```

Logistic regression is the default model. To compare a random forest, create a
second experiment and run:

```bash
hiraku record create sentiment-classification/random-forest \
  --title "Random forest" \
  --parent sentiment-classification \
  --type experiment \
  --commit HEAD

hiraku run --record sentiment-classification/random-forest -- \
  uv run python train.py --model random-forest --seed 0
```

Both models log `val/accuracy`, `val/loss`, and training duration, along with
the model, seed, and feature settings. Hiraku supplies record and job IDs for
run attribution.

## Inspect the runs

```bash
hiraku leaderboard runs --record sentiment-classification/logistic-regression
hiraku leaderboard runs --record sentiment-classification/random-forest
```

Start the local MLflow UI with:

```bash
uv run mlflow ui --backend-store-uri sqlite:///mlflow.db
```
