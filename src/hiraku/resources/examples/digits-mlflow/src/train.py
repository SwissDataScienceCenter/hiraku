"""Log epoch-by-epoch Digits learning curves to MLflow."""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import mlflow
import numpy as np
from sklearn.datasets import load_digits
from sklearn.metrics import accuracy_score, log_loss
from sklearn.model_selection import train_test_split
from sklearn.neural_network import MLPClassifier
from threadpoolctl import threadpool_limits
from tqdm import tqdm


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alpha", type=float, default=0.0001)
    parser.add_argument("--epochs", type=int, default=40)
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
    required = (
        "HIRAKU_TASK_ID",
        "HIRAKU_EXPERIMENT_ID",
        "HIRAKU_COMMIT",
        "HIRAKU_ENTRYPOINT",
    )
    missing = [name for name in required if name not in os.environ]
    if missing:
        raise RuntimeError(
            f"Missing launch environment variables: {', '.join(missing)}"
        )
    tags = {
        f"hiraku.{key}": os.environ[f"HIRAKU_{key.upper()}"]
        for key in ("task_id", "experiment_id", "commit", "entrypoint")
    }
    database = Path(os.environ["HIRAKU_ROOT"]) / "mlflow.db"
    default_tracking_uri = f"sqlite:///{database}"
    mlflow.set_tracking_uri(
        os.environ.get("MLFLOW_TRACKING_URI", default_tracking_uri)
    )
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
    with mlflow.start_run(run_name=tags["hiraku.experiment_id"], tags=tags):
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
        started = time.perf_counter()
        # Limit BLAS threads to avoid overhead on this small CPU workload.
        with threadpool_limits(limits=1):
            for epoch in tqdm(range(1, args.epochs + 1)):
                # partial_fit performs one epoch and preserves optimizer state.
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
        mlflow.log_metric("runtime/seconds", time.perf_counter() - started)


if __name__ == "__main__":
    main()
