"""Generate measured Digits results with a portable, metrics-only MLflow database."""

from __future__ import annotations

import argparse
import math
import shutil
import sqlite3
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from hiraku import HirakuConfig
from hiraku.launch import launch
from hiraku.record import Record

EXPERIMENTS = (("weak-l2", 0.0001), ("strong-l2", 1.0))
EPOCHS = 20


def validate_database(database: Path) -> None:
    """Verify six completed, attributed runs and their validation measurements."""
    with sqlite3.connect(database) as connection:
        runs = connection.execute(
            "SELECT r.run_uuid, r.status, t.value FROM runs r JOIN tags t "
            "ON r.run_uuid=t.run_uuid WHERE t.key='hiraku.record_id'"
        ).fetchall()
        if Counter(record for _, _, record in runs) != {"weak-l2": 3, "strong-l2": 3}:
            raise ValueError("Expected three runs per regularization experiment")
        for run, status, record in runs:
            params = dict(
                connection.execute(
                    "SELECT key,value FROM params WHERE run_uuid=?", (run,)
                )
            )
            metrics = dict(
                connection.execute(
                    "SELECT key,value FROM latest_metrics WHERE run_uuid=?", (run,)
                )
            )
            tags = dict(
                connection.execute(
                    "SELECT key,value FROM tags WHERE run_uuid=?", (run,)
                )
            )
            if status != "FINISHED" or tags.get("hiraku.entrypoint") != "train":
                raise ValueError("Incomplete or unattributed training run")
            if (
                float(params["alpha"]) != dict(EXPERIMENTS)[record]
                or int(params["epochs"]) != EPOCHS
            ):
                raise ValueError("Unexpected training parameters")
            if not all(
                math.isfinite(metrics[m]) for m in ("val/log_loss", "val/accuracy")
            ):
                raise ValueError("Missing or non-finite validation metric")
        for record, _ in EXPERIMENTS:
            seeds = [
                dict(
                    connection.execute(
                        "SELECT key,value FROM params WHERE run_uuid=?", (run,)
                    )
                )["seed"]
                for run, _, name in runs
                if name == record
            ]
            if sorted(seeds) != ["1", "2", "3"]:
                raise ValueError("Unexpected experiment seeds")


def generate(config: HirakuConfig) -> None:
    """Run a small local batch without contacting a remote tracking service."""
    database = config.root / "mlflow.db"
    if database.exists() or list(config.records_dir.rglob("*.md")):
        raise FileExistsError("Generation requires an example without records or runs")
    config.tracking_backend = {
        "type": "mlflow",
        "database": "mlflow.db",
        "experiment_name": "hiraku-digits",
    }
    config.save()
    Record.create(
        "digits",
        type="task",
        status="concluded",
        activate=False,
        config=config,
        metrics=[
            {"name": "val/log_loss", "direction": "minimize"},
            {"name": "val/accuracy", "direction": "maximize"},
        ],
        markdown="# Digits regularization\n\n"
        "Compare weak and strong L2 regularization over three seeds.\n"
        "Measured CPU training results; no statistical significance is claimed.\n",
    )
    for identifier, alpha in EXPERIMENTS:
        record = Record.create(
            identifier,
            parent_id="digits",
            type="hypothesis",
            status="concluded",
            activate=False,
            config=config,
            markdown=f"## {identifier}\n\nL2 alpha: {alpha}; seeds: 1, 2, 3; "
            f"epochs: {EPOCHS}; hidden units: 64; validation fraction: 0.25.\n",
            entrypoints={
                "train": {
                    "command": ["uv", "run", "python", "train.py"],
                    "args": {
                        "alpha": alpha,
                        "epochs": EPOCHS,
                        "hidden_units": 64,
                        "validation_fraction": 0.25,
                    },
                }
            },
        )
        for seed in (1, 2, 3):
            launch(
                record.entrypoint("train", config=config),
                name="train",
                record_id=identifier,
                args={"seed": seed},
                config=config,
                env={
                    "MLFLOW_TRACKING_URI": f"sqlite:///{database}",
                    "MLFLOW_EXPERIMENT_NAME": "hiraku-digits",
                },
            )
    validate_database(database)
    config.active_record_id = "weak-l2"
    config.save()


def generate_results(output: Path) -> None:
    """Export records, configuration and a relocatable SQLite snapshot."""
    output = output.absolute()
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    with TemporaryDirectory(prefix="hiraku-digits-") as temporary:
        config = HirakuConfig.init(
            Path(temporary).resolve() / "project",
            example="digits-mlflow",
            install_skill=False,
        )
        generate(config)
        output.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".results-", dir=output.parent) as staging:
            overlay = Path(staging) / "overlay"
            shutil.copytree(config.records_dir, overlay / "hiraku/records")
            shutil.copyfile(config.path, overlay / "hiraku.yml")
            with (
                sqlite3.connect(config.root / "mlflow.db") as source,
                sqlite3.connect(overlay / "mlflow.db") as snapshot,
            ):
                source.backup(snapshot)
                # The training script logs metrics only; no artifacts to relocate.
                snapshot.execute(
                    "UPDATE experiments SET artifact_location="
                    "'mlruns/' || experiment_id"
                )
                snapshot.execute(
                    "UPDATE runs SET artifact_uri='mlruns/' || "
                    "experiment_id || '/' || run_uuid || '/artifacts'"
                )
            validate_database(overlay / "mlflow.db")
            overlay.rename(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, help="Export an isolated results overlay"
    )
    args = parser.parse_args()
    if args.output is None:
        generate(HirakuConfig.discover())
    else:
        generate_results(args.output)
