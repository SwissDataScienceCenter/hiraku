"""Train Iris models and log reproducible results to Weights & Biases."""

from __future__ import annotations

import argparse
import time

import wandb
from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score, log_loss
from sklearn.model_selection import train_test_split

from hiraku import track_run


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model", choices=("logistic", "random_forest"), default="logistic"
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--test-size", type=float, default=0.25)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with track_run(
        backend="wandb",
        project="iris-wandb",
        config={
            "model": args.model,
            "seed": args.seed,
            "test_size": args.test_size,
        },
    ) as run:
        started = time.perf_counter()
        data = load_iris()
        train_x, test_x, train_y, test_y = train_test_split(
            data.data,
            data.target,
            test_size=args.test_size,
            random_state=args.seed,
            stratify=data.target,
        )
        model = (
            LogisticRegression(max_iter=500, random_state=args.seed)
            if args.model == "logistic"
            else RandomForestClassifier(
                n_estimators=100, random_state=args.seed, n_jobs=1
            )
        )
        model.fit(train_x, train_y)
        probabilities = model.predict_proba(test_x)
        predictions = model.predict(test_x)
        metrics = {
            "test/accuracy": float(accuracy_score(test_y, predictions)),
            "test/f1_macro": float(f1_score(test_y, predictions, average="macro")),
            "test/log_loss": float(log_loss(test_y, probabilities)),
            "runtime/seconds": time.perf_counter() - started,
        }
        run.log(metrics)
        wandb.sklearn.plot_classifier(
            model,
            train_x,
            test_x,
            train_y,
            test_y,
            predictions,
            probabilities,
            labels=list(data.target_names),
            model_name=args.model,
        )
        print(" ".join(f"{name}={value:.6f}" for name, value in metrics.items()))


if __name__ == "__main__":
    main()
