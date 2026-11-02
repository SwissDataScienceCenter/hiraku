"""Train Iris models and log reproducible results to Weights & Biases."""

from __future__ import annotations

import argparse
import os
import random

import wandb
from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score, log_loss
from sklearn.model_selection import train_test_split
from wandb import sklearn as wandb_sklearn

FIELDS = ["record_id", "git_state"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model", choices=("logistic", "random_forest"), default="logistic"
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--test-size", type=float, default=0.25)
    return parser.parse_args()


def hiraku_config() -> dict[str, str]:
    """Return available Hiraku launch attribution for W&B."""
    record_id = os.getenv("HIRAKU_RECORD_ID")
    if not record_id:
        return {}
    return {
        f"hiraku.{field}": value
        for field in FIELDS
        if (value := os.getenv(f"HIRAKU_{field.upper()}"))
        and not (field == "git_state" and value == "unavailable")
    }


def main() -> None:
    args = parse_args()
    run_config = hiraku_config()
    with wandb.init(
        project="iris-wandb",
        name=os.environ.get("WANDB_RUN_NAME"),
        tags=["hiraku", "iris-wandb", args.model],
        config={
            **run_config,
            "model": args.model,
            "seed": args.seed,
            "test_size": args.test_size,
        },
    ) as run:
        random.seed(args.seed)
        data = load_iris()
        train_x, test_x, train_y, test_y = train_test_split(
            data.data,
            data.target,
            test_size=args.test_size,
            random_state=args.seed,
            stratify=data.target,
        )
        if args.model == "logistic":
            model = LogisticRegression(max_iter=500, random_state=args.seed)
        else:
            model = RandomForestClassifier(n_estimators=100, random_state=args.seed)
        model.fit(train_x, train_y)
        probabilities = model.predict_proba(test_x)
        predictions = model.predict(test_x)
        wandb_sklearn.plot_classifier(
            model,
            train_x,
            test_x,
            train_y,
            test_y,
            predictions,
            probabilities,
            labels=data.target_names,
            model_name=args.model,
        )
        wandb_sklearn.plot_summary_metrics(model, train_x, train_y, test_x, test_y)

        metrics = {
            "test/accuracy": float(accuracy_score(test_y, predictions)),
            "test/f1_macro": float(f1_score(test_y, predictions, average="macro")),
            "test/log_loss": float(log_loss(test_y, probabilities)),
        }
        run.log(metrics)
        run.log_code()
        print(" ".join(f"{name}={value:.6f}" for name, value in metrics.items()))


if __name__ == "__main__":
    main()
