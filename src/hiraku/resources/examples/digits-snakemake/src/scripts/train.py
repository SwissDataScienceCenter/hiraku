"""Train one Digits variant from prepared input and log curves to MLflow."""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path

import mlflow
import numpy as np
from sklearn.metrics import accuracy_score, log_loss
from sklearn.neural_network import MLPClassifier
from threadpoolctl import threadpool_limits
from tqdm import tqdm


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--alpha", type=float, default=0.0001)
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--hidden-units", type=int, default=64)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--correlation-id", default=None)
    args = parser.parse_args()
    if args.epochs < 1 or args.hidden_units < 1:
        parser.error("epochs and hidden-units must be positive")
    if not np.isfinite(args.alpha) or args.alpha < 0:
        parser.error("alpha must be finite and nonnegative")
    return args


def save_atomically(path: Path, value: dict) -> None:
    """Replace ``path`` only after the complete result was written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def require_environment() -> dict[str, str]:
    required = (
        "HIRAKU_RECORD_ID",
        "HIRAKU_COMMIT",
        "HIRAKU_ENTRYPOINT",
        "HIRAKU_ROOT",
    )
    missing = [name for name in required if name not in os.environ]
    if missing:
        raise RuntimeError(
            f"Missing launch environment variables: {', '.join(missing)}"
        )
    return {name: os.environ[name] for name in required}


def main() -> None:
    args = parse_args()
    if args.correlation_id is None:
        args.correlation_id = os.environ.get("HIRAKU_WORKFLOW_RUN_ID")
    environment = require_environment()
    tags = {
        "hiraku.record_id": environment["HIRAKU_RECORD_ID"],
        "hiraku.commit": environment["HIRAKU_COMMIT"],
        "hiraku.entrypoint": environment["HIRAKU_ENTRYPOINT"],
    }
    if args.correlation_id:
        tags["hiraku.workflow_run_id"] = args.correlation_id
    database = Path(environment["HIRAKU_ROOT"]) / "mlflow.db"
    default_tracking_uri = f"sqlite:///{database}"
    mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", default_tracking_uri))
    mlflow.set_experiment(os.environ.get("MLFLOW_EXPERIMENT_NAME", "hiraku-digits"))

    with np.load(args.input) as prepared:
        train_x = prepared["train_x"]
        train_y = prepared["train_y"]
        val_x = prepared["val_x"]
        val_y = prepared["val_y"]

    model = MLPClassifier(
        hidden_layer_sizes=(args.hidden_units,),
        alpha=args.alpha,
        solver="adam",
        batch_size=64,
        learning_rate_init=0.001,
        random_state=args.seed,
    )
    history: dict[str, list[float]] = {}
    with mlflow.start_run(run_name=tags["hiraku.record_id"], tags=tags):
        mlflow.log_params(
            {
                "alpha": args.alpha,
                "epochs": args.epochs,
                "hidden_units": args.hidden_units,
                "seed": args.seed,
                "model": "mlp",
                "dataset": "digits",
                "split_input": str(args.input),
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
                for name, value in metrics.items():
                    history.setdefault(name, []).append(value)
                mlflow.log_metrics(metrics, step=epoch)
                print(
                    f"epoch={epoch} "
                    + " ".join(f"{name}={value:.4f}" for name, value in metrics.items())
                )
        runtime = time.perf_counter() - started
        mlflow.log_metric("runtime/seconds", runtime)
        run_id = mlflow.active_run().info.run_id

    final = {name: values[-1] for name, values in history.items()}
    if not all(math.isfinite(value) for value in final.values()):
        raise RuntimeError("Training produced non-finite metrics")
    save_atomically(
        args.output,
        {
            "record_id": tags["hiraku.record_id"],
            "entrypoint": tags["hiraku.entrypoint"],
            "commit": tags["hiraku.commit"],
            "workflow_run_id": args.correlation_id,
            "mlflow_run_id": run_id,
            "alpha": args.alpha,
            "epochs": args.epochs,
            "hidden_units": args.hidden_units,
            "seed": args.seed,
            "runtime_seconds": runtime,
            "final_metrics": final,
            "history": history,
        },
    )


if __name__ == "__main__":
    main()
