"""Log epoch-by-epoch Digits learning curves to MLflow."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import mlflow
import numpy as np
from sklearn.datasets import load_digits
from sklearn.metrics import accuracy_score, log_loss
from sklearn.model_selection import train_test_split
from sklearn.neural_network import MLPClassifier
from tqdm import tqdm

FIELDS = [
    "record_id",
    "git_state",
]


def hiraku_tags() -> dict[str, str]:
    """Return available Hiraku launch attribution as MLflow tags."""
    record_id = os.environ.get("HIRAKU_RECORD_ID")
    if not record_id:
        return {}
    return {
        f"hiraku.{field}": value
        for field in FIELDS
        if (value := os.environ.get(f"HIRAKU_{field.upper()}"))
        and not (field == "git_state" and value == "unavailable")
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alpha", type=float, default=0.0001)
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--hidden-units", type=int, default=64)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--validation-fraction", type=float, default=0.25)
    args = parser.parse_args()
    if args.epochs < 1 or args.hidden_units < 1:
        parser.error("epochs and hidden-units must be positive")
    if not np.isfinite(args.alpha) or args.alpha < 0:
        parser.error("alpha must be finite and nonnegative")
    if not 0 < args.validation_fraction < 1:
        parser.error("validation-fraction must be between zero and one")
    return args


def main() -> None:
    args = parse_args()
    tags = hiraku_tags()
    database = Path(os.environ.get("HIRAKU_TRACKING_ROOT", ".")) / "mlflow.db"
    default_tracking_uri = f"sqlite:///{database}"
    mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", default_tracking_uri))
    mlflow.set_experiment(os.environ.get("MLFLOW_EXPERIMENT_NAME", "hiraku-digits"))
    data = load_digits()
    # Pixels have a known range of 0..16; no statistics are fit on validation data.
    train_x, val_x, train_y, val_y = train_test_split(
        data.data / 16.0,
        data.target,
        test_size=args.validation_fraction,
        random_state=args.seed,
        stratify=data.target,
    )
    model = MLPClassifier(
        hidden_layer_sizes=(args.hidden_units,),
        alpha=args.alpha,
        solver="adam",
        batch_size=64,
        learning_rate_init=0.001,
        random_state=args.seed,
    )
    with mlflow.start_run(run_name=tags.get("hiraku.record_id"), tags=tags):
        mlflow.log_params(
            {
                **vars(args),
                "model": "mlp",
                "dataset": "digits",
                "train_samples": len(train_y),
                "validation_samples": len(val_y),
                "learning_rate_init": 0.001,
                "batch_size": 64,
            }
        )
        for epoch in tqdm(range(1, args.epochs + 1)):
            model.partial_fit(train_x, train_y, classes=np.arange(10))
            metrics = {}
            for split, features, labels in (
                ("train", train_x, train_y),
                ("val", val_x, val_y),
            ):
                probabilities = model.predict_proba(features)
                metrics[f"{split}/log_loss"] = float(
                    log_loss(labels, probabilities, labels=model.classes_)
                )
                metrics[f"{split}/accuracy"] = float(
                    accuracy_score(labels, model.classes_[probabilities.argmax(1)])
                )
            mlflow.log_metrics(metrics, step=epoch)
            print(
                f"epoch={epoch} "
                + " ".join(f"{name}={value:.4f}" for name, value in metrics.items())
            )


if __name__ == "__main__":
    main()
