"""Create a Digits MLflow project with nine runs and open it in the dashboard.

Run from the Hiraku checkout:

    uv run --all-extras python scripts/dashboard_demo.py

Three child records (weak, mid, and strong L2 regularization) each train with
seeds 1, 2, and 3. Launches are interleaved so the dashboard's run-number axis
mixes records. The MLflow UI and the dashboard are then served until Ctrl-C.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from hiraku.config import HirakuConfig
from hiraku.dashboard.data import load
from hiraku.dashboard.launcher import DEFAULT_PORT, serve_dashboard
from hiraku.launch import launch
from hiraku.record import Record, load_record

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS = {"weak-l2": 0.0001, "mid-l2": 0.01, "strong-l2": 1.0}
SEEDS = (1, 2, 3)


def git(directory: Path, *arguments: str) -> str:
    identity = ["-c", "user.name=Hiraku demo", "-c", "user.email=demo@example.invalid"]
    return subprocess.run(
        ["git", *identity, *arguments],
        cwd=directory,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def create_project(directory: Path, *, epochs: int, mlflow_port: int) -> HirakuConfig:
    """Initialize the Digits example and create the record tree."""
    git(directory, "init", "-b", "main")
    config = HirakuConfig.init(directory, example="digits-mlflow", install_skill=False)
    git(directory, "add", ".")
    git(directory, "commit", "-m", "Initialize Digits dashboard demo")
    commit = git(directory, "rev-parse", "HEAD")
    Record.create(
        "digits",
        type="task",
        goal="Understand how L2 regularization affects Digits learning curves",
        metrics=[
            {"name": "val/log_loss", "direction": "minimize"},
            {"name": "val/accuracy", "direction": "maximize"},
        ],
        tracking_backend={
            "type": "mlflow",
            "database": "mlflow.db",
            "experiment_name": "hiraku-digits",
            "ui_url": f"http://127.0.0.1:{mlflow_port}",
        },
        activate=False,
        config=config,
    )
    for record_id, alpha in EXPERIMENTS.items():
        Record.create(
            record_id,
            parent_id="digits",
            type="experiment",
            summary=f"L2 regularization alpha={alpha}",
            commit=commit,
            entrypoints={
                "train": {
                    # Reuse this checkout's environment instead of syncing a
                    # second one for the example project.
                    "command": [
                        "uv",
                        "run",
                        "--project",
                        str(ROOT),
                        "--all-extras",
                        "python",
                        "train.py",
                    ],
                    "args": {"alpha": alpha, "epochs": epochs, "hidden-units": 64},
                }
            },
            activate=False,
            config=config,
        )
    return config


def launch_runs(config: HirakuConfig) -> None:
    total = len(SEEDS) * len(EXPERIMENTS)
    number = 0
    for seed in SEEDS:
        for record_id in EXPERIMENTS:
            number += 1
            print(f"[{number}/{total}] {record_id} seed={seed}", flush=True)
            entrypoint = load_record(record_id, config=config).entrypoint(
                "train", config=config
            )
            result = launch(
                entrypoint,
                name="train",
                args={"seed": seed},
                record_id=record_id,
                capture_output=True,
                check=False,
                config=config,
            )
            if result.returncode:
                raise SystemExit(f"Training failed:\n{result.stdout}{result.stderr}")


def check_runs(config: HirakuConfig) -> None:
    project = load(config)
    expected = len(SEEDS) * len(EXPERIMENTS)
    if project.errors or len(project.runs) != expected:
        raise SystemExit(
            f"Expected {expected} runs, found {len(project.runs)}: {project.errors}"
        )
    if not all(run.url for run in project.runs):
        raise SystemExit("Every MLflow run should link to the MLflow UI")


def serve(config: HirakuConfig, *, port: int, mlflow_port: int, no_browser: bool):
    mlflow_ui = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "mlflow",
            "ui",
            "--backend-store-uri",
            f"sqlite:///{config.root / 'mlflow.db'}",
            "--host",
            "127.0.0.1",
            "--port",
            str(mlflow_port),
        ],
        cwd=config.root,
    )
    print(f"MLflow UI: http://127.0.0.1:{mlflow_port}")
    print(f"Dashboard: http://127.0.0.1:{port}  (Ctrl-C stops both)", flush=True)
    try:
        return serve_dashboard(
            config, record_id="digits", port=port, no_browser=no_browser
        )
    except KeyboardInterrupt:
        return 0
    finally:
        mlflow_ui.terminate()
        mlflow_ui.wait(timeout=10)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, help="new or empty project directory")
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--mlflow-port", type=int, default=5000)
    parser.add_argument("--no-serve", action="store_true", help="stop after runs")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.epochs < 1:
        parser.error("--epochs must be positive")

    if args.dir is None:
        directory = Path(tempfile.mkdtemp(prefix="hiraku-digits-demo-"))
    else:
        directory = args.dir.expanduser().resolve()
        if directory.exists() and any(directory.iterdir()):
            parser.error(f"{directory} is not empty")
        directory.mkdir(parents=True, exist_ok=True)
    print(f"Demo project: {directory}", flush=True)

    config = create_project(directory, epochs=args.epochs, mlflow_port=args.mlflow_port)
    launch_runs(config)
    check_runs(config)
    if args.no_serve:
        print(f"Serve later with: cd {directory} && hiraku dashboard --record digits")
        return 0
    return serve(
        config,
        port=args.port,
        mlflow_port=args.mlflow_port,
        no_browser=args.no_browser,
    )


if __name__ == "__main__":
    raise SystemExit(main())
