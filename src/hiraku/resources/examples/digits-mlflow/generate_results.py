"""Generate measured Digits results with a portable, metrics-only MLflow database."""

from __future__ import annotations

import argparse
import math
import shutil
import sqlite3
import subprocess
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from hiraku import Project
from hiraku.execution import prepare_job, run_local

EXPERIMENTS = (("weak-l2", 0.0001), ("strong-l2", 1.0))
EPOCHS = 20


def validate_database(database: Path, record_names: dict[str, str]) -> None:
    """Verify six completed, attributed runs and their validation measurements."""
    with sqlite3.connect(database) as connection:
        runs = connection.execute(
            "SELECT r.run_uuid, r.status, t.value FROM runs r JOIN tags t "
            "ON r.run_uuid=t.run_uuid WHERE t.key='hiraku.record_id'"
        ).fetchall()
        expected = Counter({record_names[name]: 3 for name, _ in EXPERIMENTS})
        if Counter(record for _, _, record in runs) != expected:
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
            if status != "FINISHED" or tags.get("hiraku.record_id") != record:
                raise ValueError("Incomplete or unattributed training run")
            if (
                float(params["alpha"])
                != dict(EXPERIMENTS)[
                    next(name for name, rid in record_names.items() if rid == record)
                ]
                or int(params["epochs"]) != EPOCHS
            ):
                raise ValueError("Unexpected training parameters")
            if not all(
                math.isfinite(metrics[m]) for m in ("val/log_loss", "val/accuracy")
            ):
                raise ValueError("Missing or non-finite validation metric")
        for name, _ in EXPERIMENTS:
            record = record_names[name]
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


def generate(project: Project) -> dict[str, str]:
    """Run a small local batch without contacting a remote tracking service."""
    database = project.root / "mlflow.db"
    if database.exists() or list(project.records_dir.rglob("*.md")):
        raise FileExistsError("Generation requires an example without records or runs")
    root = project.vault.create(
        "digit-classification",
        type="task",
        state="concluded",
        markdown="# Digits regularization\n\n"
        "Compare weak and strong L2 regularization over three seeds.\n"
        "Measured CPU training results; no statistical significance is claimed.\n",
        title="Digit classification",
    )
    record_names = {}
    for identifier, alpha in EXPERIMENTS:
        record = project.vault.create(
            f"digit-classification/{identifier}",
            title=f"{identifier.split('-')[0].capitalize()} L2 regularization",
            parent_id=root.id,
            type="hypothesis",
            state="concluded",
            markdown=f"## {identifier}\n\nL2 alpha: {alpha}; seeds: 1, 2, 3; "
            f"epochs: {EPOCHS}; hidden units: 64; validation fraction: 0.25.\n",
        )
        record_names[identifier] = record.id
        command = [
            "uv",
            "run",
            "python",
            "train.py",
            "--alpha",
            str(alpha),
            "--epochs",
            str(EPOCHS),
            "--hidden-units",
            str(64),
            "--validation-fraction",
            str(0.25),
        ]
        for seed in (1, 2, 3):
            job = prepare_job(
                [*command, "--seed", str(seed)],
                record_id=record.id,
                project=project,
                directory=project.root,
                env={
                    "MLFLOW_TRACKING_URI": f"sqlite:///{database}",
                    "MLFLOW_EXPERIMENT_NAME": "hiraku-digits",
                },
            )
            status = run_local(job)
            if status.exit_code:
                raise RuntimeError(
                    f"Training command for {identifier} failed with "
                    f"exit code {status.exit_code}"
                )
    validate_database(database, record_names)
    project.set_active_record(record_names["weak-l2"])
    return record_names


def generate_results(output: Path) -> None:
    """Export records, configuration and a relocatable SQLite snapshot."""
    output = output.absolute()
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    with TemporaryDirectory(prefix="hiraku-digits-") as temporary:
        project = Project.create(
            Path(temporary).resolve() / "project",
            example="digits-mlflow",
            install_skill=False,
        )
        for arguments in (
            ["init", "-q"],
            ["add", "."],
            [
                "-c",
                "user.name=Hiraku example",
                "-c",
                "user.email=example@hiraku.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "Initialize isolated example source",
            ],
        ):
            subprocess.run(["git", *arguments], cwd=project.root, check=True)
        record_ids = generate(project)
        output.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".results-", dir=output.parent) as staging:
            overlay = Path(staging) / "overlay"
            shutil.copytree(project.records_dir, overlay / "hiraku/records")
            shutil.copytree(project.leaderboards_dir, overlay / "hiraku/leaderboards")
            shutil.copyfile(project.path, overlay / "hiraku.yml")
            with (
                sqlite3.connect(project.root / "mlflow.db") as source,
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
            validate_database(overlay / "mlflow.db", record_ids)
            overlay.rename(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, help="Export an isolated results overlay"
    )
    args = parser.parse_args()
    if args.output is None:
        generate(Project.discover())
    else:
        generate_results(args.output)
