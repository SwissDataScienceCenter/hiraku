# Rotten Tomatoes sentiment classification

Compare a logistic regression baseline with a random forest on the
[Rotten Tomatoes movie review dataset](https://huggingface.co/datasets/cornell-movie-review-data/rotten_tomatoes).
The training script uses TF-IDF features and logs parameters and validation
metrics to MLflow. Hiraku's default leaderboard reads those MLflow runs.

## Create the project

Install Hiraku, then create and enter the example project:

```bash
hiraku example create rotten-tomatoes --git
cd rotten-tomatoes
uv sync
git add .
git commit -m "initialize Rotten Tomatoes example"
```

### Want to use your agent to run the example?

Use your agent to run the example with:

```
Create a task record for sentiment classification.
Create an experiment record for the logistic regression baseline.
Run the logistic regression baseline with two seeds.
Then create an experiment record for the random forest and run it with two seeds.
Finally, inspect the runs in the leaderboard.
```

## Create a task record

```bash
hiraku record create sentiment-classification \
  --title "Sentiment classification" \
  --type task \
  --set "goal=Compare text classifiers on Rotten Tomatoes reviews"
```

## Create an experiment record

```bash
hiraku record create sentiment-classification/logistic-regression \
  --title "Logistic regression baseline" \
  --parent sentiment-classification \
  --type experiment \
  --commit HEAD
```

## Train and compare models

Run the baseline with two seeds:

```bash
hiraku job submit --record sentiment-classification/logistic-regression -- uv run python train.py --model logistic-regression
```

The default model is logistic regression. To compare a random forest, create a separate experiment record and run:

```bash
hiraku record create sentiment-classification/random-forest \
  --title "Random forest" \
  --parent sentiment-classification \
  --type experiment \
  --commit HEAD

hiraku job submit --record sentiment-classification/random-forest -- uv run python train.py --model random-forest
```

Both models log `val/accuracy`, `val/loss`, and training duration, along with
the model, seed, and feature settings. Hiraku supplies record and job IDs to
each run so the leaderboard can attribute it to the correct experiment.

## Inspect the runs

```bash
hiraku leaderboard runs
```

The `default` leaderboard reads from MLflow. You can open the local MLflow UI
with `uv run mlflow ui --backend-store-uri sqlite:///mlflow.db`.
