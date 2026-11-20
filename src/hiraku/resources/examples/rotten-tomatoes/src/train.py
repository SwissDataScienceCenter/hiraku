"""Train a text classifier and log its validation metrics to MLflow."""

import argparse
import os
from pathlib import Path
from time import perf_counter

import mlflow
from datasets import load_dataset
from filelock import FileLock
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, log_loss
from sklearn.pipeline import make_pipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        choices=("logistic-regression", "random-forest"),
        default="logistic-regression",
    )
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data = load_dataset("cornell-movie-review-data/rotten_tomatoes")
    train = data["train"]
    validation = data["validation"]

    if args.model == "logistic-regression":
        classifier = LogisticRegression(max_iter=200, random_state=args.seed)
    else:
        classifier = RandomForestClassifier(
            n_estimators=200,
            n_jobs=-1,
            random_state=args.seed,
        )
    model = make_pipeline(TfidfVectorizer(max_features=5000), classifier)

    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI")
    if not tracking_uri:
        tracking_uri = f"sqlite:///{Path.cwd() / 'mlflow.db'}"
    mlflow.set_tracking_uri(tracking_uri)
    experiment_name = os.environ.get("MLFLOW_EXPERIMENT_NAME", Path.cwd().name)
    # MLflow's SQLite schema migration is not safe when fresh runs initialize
    # the same database at the same time.
    with FileLock(Path.cwd() / ".mlflow-init.lock"):
        mlflow.set_experiment(experiment_name)

    started = perf_counter()
    model.fit(train["text"], train["label"])
    duration = perf_counter() - started

    predictions = model.predict(validation["text"])
    probabilities = model.predict_proba(validation["text"])
    metrics = {
        "val/accuracy": accuracy_score(validation["label"], predictions),
        "val/loss": log_loss(validation["label"], probabilities),
        "train/duration_seconds": duration,
    }
    params = {
        "model": args.model,
        "seed": args.seed,
        "tfidf_max_features": 5000,
    }
    if args.model == "random-forest":
        params["n_estimators"] = 200

    with mlflow.start_run(run_name=f"{args.model}-seed-{args.seed}"):
        mlflow.log_params(params)
        mlflow.log_metrics(metrics)
        tags = {
            "hiraku.record_id": os.environ.get("HIRAKU_RECORD_ID", ""),
            "hiraku.job_id": os.environ.get("HIRAKU_JOB_ID", ""),
        }
        mlflow.set_tags({key: value for key, value in tags.items() if value})
    print(metrics)


if __name__ == "__main__":
    main()
