"""Check the dashboard, run the Digits example, and launch it locally."""

from __future__ import annotations

import argparse
import csv
import json
import os
import runpy
import signal
import socket
import subprocess
import tempfile
import time
import webbrowser
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
MARKER = ".hiraku-dashboard-demo"
CSV_MARKER = "Hiraku dashboard demo, version 1\n"
DIGITS_MARKER = "Hiraku dashboard digits-mlflow demo, version 1\n"


def demo_directory(directory: Path | None = None) -> Path:
    """Allocate an empty directory, refusing to overwrite existing files."""
    if directory is None:
        directory = Path(tempfile.mkdtemp(prefix="hiraku-dashboard-demo-"))
    directory = directory.expanduser().resolve()
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        raise ValueError(f"Demo directory must be empty: {directory}")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def launch_digits_run(directory: Path, record_id: str) -> None:
    """Launch the example against its own local MLflow database."""
    from hiraku.config import HirakuConfig
    from hiraku.launch import launch
    from hiraku.record import load_record

    directory = directory.expanduser().resolve()
    marker = directory / MARKER
    if not marker.is_file() or marker.read_text(encoding="utf-8") != DIGITS_MARKER:
        raise ValueError(f"Not a Digits dashboard demo: {directory}")
    if record_id not in {"weak-l2", "strong-l2"}:
        raise ValueError("Digits record must be weak-l2 or strong-l2")
    config = HirakuConfig.load(directory / "hiraku.yml")
    record = load_record(record_id, config=config).resolve(config=config)
    launch(
        record.values["entrypoints"]["train"],
        name="train",
        record_id=record_id,
        config=config,
        env={
            "MLFLOW_TRACKING_URI": f"sqlite:///{directory / 'mlflow.db'}",
            "MLFLOW_EXPERIMENT_NAME": "hiraku-digits",
            "MLFLOW_ENABLE_ASYNC_LOGGING": "false",
        },
    )


def verify_digits_demo(directory: Path) -> None:
    """Check that real tracked runs reach both dashboard plot transformations."""
    from hiraku.config import HirakuConfig
    from hiraku.dashboard import build_snapshot
    from hiraku.dashboard.plots import pareto_points, progress_points

    config = HirakuConfig.load(directory / "hiraku.yml")
    snapshot = build_snapshot(config, record_id="weak-l2", scope="source")
    if snapshot.errors:
        raise ValueError(f"Dashboard could not read Digits runs: {snapshot.errors}")
    if {run.record_id for run in snapshot.runs} != {"weak-l2", "strong-l2"}:
        raise ValueError("Dashboard must contain runs for both Digits experiments")
    required_metrics = {
        "train/log_loss",
        "val/log_loss",
        "train/accuracy",
        "val/accuracy",
    }
    if any(
        not required_metrics <= run.metrics.keys()
        or not run.commit
        or run.entrypoint != "train"
        for run in snapshot.runs
    ):
        raise ValueError("Digits runs are missing metrics or launch provenance")
    progress = progress_points(snapshot.runs, "val/log_loss", direction="minimize")
    pareto = pareto_points(
        snapshot.runs,
        "val/accuracy",
        "val/log_loss",
        direction_x="maximize",
        direction_y="minimize",
    )
    if progress.omitted or pareto.omitted:
        raise ValueError("Digits runs contain missing or non-finite plot metrics")
    if any(
        not run.url
        or not run.url.startswith("http://127.0.0.1:")
        or f"/runs/{run.id}" not in run.url
        for run in snapshot.runs
    ):
        raise ValueError("Digits runs must link to their native MLflow run pages")
    print(
        f"Verified {len(snapshot.runs)} real MLflow runs: "
        f"{len(progress.frame)} progress points, {len(pareto.frame)} Pareto points.",
        flush=True,
    )


def create_digits_demo(
    directory: Path | None = None,
    *,
    epochs: int = 40,
    mlflow_port: int = 5000,
) -> Path:
    """Initialize and run the bundled Digits example with committed attribution."""
    from hiraku.config import HirakuConfig
    from hiraku.record import Record

    if epochs < 1:
        raise ValueError("epochs must be positive")
    directory = demo_directory(directory)
    print(f"Demo project: {directory}", flush=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=directory, check=True)
    config = HirakuConfig.init(directory, example="digits-mlflow", install_skill=False)
    (directory / MARKER).write_text(DIGITS_MARKER, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=directory, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Hiraku dashboard demo",
            "-c",
            "user.email=dashboard@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            "Initialize Digits MLflow dashboard demo",
        ],
        cwd=directory,
        check=True,
    )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=directory,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    Record.create(
        "digits",
        config=config,
        activate=False,
        type="task",
        goal="Understand how L2 regularization affects Digits learning curves",
        question="Does stronger regularization reduce overfitting?",
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
    )
    for record_id, alpha in (("weak-l2", 0.0001), ("strong-l2", 1.0)):
        Record.create(
            record_id,
            parent_id="digits",
            config=config,
            activate=False,
            type="experiment",
            commit=commit,
            summary=f"L2 regularization alpha={alpha}",
            entrypoints={
                "train": {
                    # Reuse the checkout's synced extras instead of a second venv.
                    "command": [
                        "uv",
                        "run",
                        "--project",
                        str(ROOT),
                        "--locked",
                        "--all-extras",
                        "python",
                        "train.py",
                    ],
                    "args": {
                        "alpha": alpha,
                        "epochs": epochs,
                        "hidden-units": 64,
                        "seed": 42,
                        "validation-fraction": 0.25,
                    },
                }
            },
        )
        print(f"Training {record_id} for {epochs} epochs…", flush=True)
        launch_digits_run(directory, record_id)
    verify_digits_demo(directory)
    return directory


def create_demo(directory: Path | None = None) -> Path:
    """Create a fresh demo, refusing to overwrite an existing project."""
    directory = demo_directory(directory)
    fixture = runpy.run_path(str(ROOT / "tests/dashboard/fixtures.py"))
    config = fixture["create_acceptance_project"](directory)

    from hiraku.record import Record

    Record.create(
        "empty",
        config=config,
        activate=False,
        tracking_backend={"type": "csv", "path": "results/empty.csv"},
    )
    # Make the source scope visibly different from the selected-record scope.
    with (directory / "results/a.csv").open(
        "a", encoding="utf-8", newline=""
    ) as stream:
        csv.writer(stream).writerow(
            ["shared-b", "child-b", "2026-01-04T00:00:00Z", "0.6", "0.4"]
        )
    (directory / MARKER).write_text(CSV_MARKER, encoding="utf-8")
    return directory


def reopen_demo(directory: Path, *, mlflow_port: int) -> tuple[Path, str, bool]:
    """Validate a retained demo and point its run links at the chosen UI port."""
    from hiraku.config import HirakuConfig
    from hiraku.record import load_record

    directory = directory.expanduser().resolve()
    marker = directory / MARKER
    if not marker.is_file():
        raise ValueError(f"Not a dashboard demo created by this script: {directory}")
    kind = marker.read_text(encoding="utf-8")
    if kind not in {CSV_MARKER, DIGITS_MARKER}:
        raise ValueError(f"Unrecognized dashboard demo marker: {directory}")
    config = HirakuConfig.load(directory / "hiraku.yml")
    if kind == CSV_MARKER:
        load_record("child-a", config=config)
        return directory, "child-a", False
    if not (directory / "mlflow.db").is_file():
        raise ValueError(f"MLflow database not found in {directory}")
    record = load_record("digits", config=config)
    backend = record.tracking_backend
    if (
        not isinstance(backend, dict)
        or backend.get("type") != "mlflow"
        or backend.get("database") != "mlflow.db"
    ):
        raise ValueError("Retained Digits demo must use its local mlflow.db backend")
    updated = {**backend, "ui_url": f"http://127.0.0.1:{mlflow_port}"}
    if updated != backend:
        record.tracking_backend = updated
        record.save(overwrite=True)
    return directory, "digits", True


def append_run(directory: Path) -> str:
    """Append a run only to a demo created by this script."""
    directory = directory.expanduser().resolve()
    if not (directory / MARKER).is_file():
        raise ValueError(f"Not a dashboard demo created by this script: {directory}")
    if (directory / MARKER).read_text(encoding="utf-8") != CSV_MARKER:
        raise ValueError(
            "--append-run requires a CSV demo; use --launch-run for Digits"
        )
    path = directory / "results/a.csv"
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != [
            "id",
            "record_id",
            "timestamp",
            "metric.acc",
            "metric.loss",
        ]:
            raise ValueError(f"Unexpected demo CSV columns: {path}")
        identifiers = {row["id"] for row in reader}
    number = 1
    while f"refreshed-{number}" in identifiers:
        number += 1
    identifier = f"refreshed-{number}"
    with path.open("a", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerow(
            [identifier, "child-a", datetime.now(UTC).isoformat(), "0.95", "0.1"]
        )
    return identifier


def port_number(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def check_port(port: int) -> None:
    """Fail before starting a server if its local port is occupied."""
    with socket.socket() as probe:
        if os.name != "nt":
            # Permit recently closed connections without permitting live listeners.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise ValueError(
                f"Port {port} is unavailable; choose another port"
            ) from exc


def wait_for_server(process: subprocess.Popen, url: str) -> None:
    """Wait for HTTP readiness, reporting startup failures promptly."""
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        code = process.poll()
        if code is not None:
            raise subprocess.CalledProcessError(code or 1, process.args)
        try:
            with urlopen(url, timeout=1):
                return
        except (URLError, TimeoutError):
            time.sleep(0.1)
    raise ValueError(f"Server did not become ready within 60 seconds: {url}")


def stop_server(process: subprocess.Popen) -> None:
    """Stop the uv process and its server workers, including nested launchers."""
    if os.name == "nt":
        if process.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
            process.wait(timeout=5)
        return
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def mlflow_training_url(base_url: str) -> str:
    """Open the demo experiment's training runs instead of MLflow's GenAI view."""
    endpoint = (
        f"{base_url}/api/2.0/mlflow/experiments/get-by-name"
        "?experiment_name=hiraku-digits"
    )
    with urlopen(endpoint, timeout=5) as response:
        experiment_id = json.load(response)["experiment"]["experiment_id"]
    return (
        f"{base_url}/#/experiments/{experiment_id}/runs?workflowType=machine_learning"
    )


def serve_demo(
    directory: Path,
    *,
    record_id: str,
    port: int,
    mlflow_port: int | None,
    no_browser: bool,
    interactive: bool = False,
) -> int:
    """Serve the dashboard and optional MLflow UI until either exits or Ctrl-C."""
    if mlflow_port == port:
        raise ValueError("Dashboard and MLflow UI must use different ports")
    check_port(port)
    if mlflow_port is not None:
        check_port(mlflow_port)
        if not (directory / "mlflow.db").is_file():
            raise ValueError(f"MLflow database not found in {directory}")
    uv = ["uv", "run", "--project", str(ROOT), "--locked", "--all-extras"]
    servers = []
    processes = []
    browser_urls = []
    try:
        if mlflow_port is not None:
            servers.append(
                (
                    [
                        *uv,
                        "mlflow",
                        "ui",
                        "--backend-store-uri",
                        f"sqlite:///{directory / 'mlflow.db'}",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(mlflow_port),
                    ],
                    f"http://127.0.0.1:{mlflow_port}",
                    "MLflow UI",
                )
            )
        _dashboard_command = [
            *uv,
            "hiraku",
            "dashboard",
            "--record",
            record_id,
            "--scope",
            "subtree" if mlflow_port is not None else "record",
            "--port",
            str(port),
            "--no-browser",
        ]
        if interactive:
            _dashboard_command.append("--interactive")
        servers.append(
            (
                _dashboard_command,
                f"http://127.0.0.1:{port}",
                "Hiraku dashboard",
            )
        )
        for command, url, label in servers:
            process = subprocess.Popen(
                command,
                cwd=directory,
                start_new_session=os.name != "nt",
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                ),
            )
            processes.append(process)
            wait_for_server(process, url)
            if label == "MLflow UI":
                url = mlflow_training_url(url)
            browser_urls.append(url)
            print(f"{label}: {url}", flush=True)
        print("Ctrl-C stops the servers. Demo files are retained.", flush=True)
        if not no_browser:
            for url in browser_urls:
                webbrowser.open(url)
        while True:
            for process in processes:
                code = process.poll()
                if code is not None:
                    if code:
                        raise subprocess.CalledProcessError(code, process.args)
                    return 0
            time.sleep(0.25)
    finally:
        for process in reversed(processes):
            stop_server(process)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--example",
        choices=["digits-mlflow", "csv"],
        default="digits-mlflow",
        help="Demo to run (default: digits-mlflow)",
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=40,
        help="Training epochs per Digits experiment (default: 40)",
    )
    parser.add_argument(
        "--project-dir", type=Path, help="Use a new or empty demo directory"
    )
    parser.add_argument(
        "--reopen",
        type=Path,
        metavar="DEMO",
        help="Serve a retained demo without retraining (updates its MLflow UI URL)",
    )
    parser.add_argument("--port", type=port_number, default=8765)
    parser.add_argument(
        "--mlflow-port",
        type=port_number,
        default=5000,
        help="MLflow UI port for the Digits demo (default: 5000)",
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="Do not open a browser"
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Start the dashboard in interactive mode",
    )
    parser.add_argument(
        "--no-serve", action="store_true", help="Create the demo, then exit"
    )
    checks = parser.add_mutually_exclusive_group()
    checks.add_argument(
        "--run-tests", action="store_true", help="Run automated checks before the demo"
    )
    checks.add_argument(
        "--skip-tests", action="store_true", help="Skip automated checks (the default)"
    )
    parser.add_argument(
        "--browser-tests",
        choices=["chrome", "webkit"],
        help="Also run the real-browser regression (requires a local browser/driver)",
    )
    parser.add_argument(
        "--append-run",
        type=Path,
        metavar="DEMO",
        help="Append a new run to an existing demo, then exit",
    )
    parser.add_argument(
        "--launch-run",
        type=Path,
        metavar="DEMO",
        help="Train an additional run in an existing Digits demo, then exit",
    )
    parser.add_argument(
        "--record",
        choices=["weak-l2", "strong-l2"],
        default="weak-l2",
        help="Digits record for --launch-run (default: weak-l2)",
    )
    args = parser.parse_args()
    try:
        if args.append_run and args.launch_run:
            parser.error("Choose either --append-run or --launch-run")
        if args.reopen and (
            args.project_dir or args.append_run or args.launch_run or args.no_serve
        ):
            parser.error(
                "--reopen cannot be combined with --project-dir, --append-run, "
                "--launch-run, or --no-serve"
            )
        if args.epochs < 1:
            parser.error("--epochs must be positive")
        if args.launch_run:
            launch_digits_run(args.launch_run, args.record)
            verify_digits_demo(args.launch_run.expanduser().resolve())
            print("Added a real MLflow run. Press Refresh in the dashboard.")
            return 0
        if args.append_run:
            identifier = append_run(args.append_run)
            print(f"Added {identifier}. Press Refresh in the dashboard.", flush=True)
            return 0
        if (
            not args.no_serve
            and not args.reopen
            and args.example == "digits-mlflow"
            and args.port == args.mlflow_port
        ):
            parser.error("--port and --mlflow-port must be different")
        if args.browser_tests and args.skip_tests:
            parser.error("--browser-tests cannot be combined with --skip-tests")
        if (
            args.project_dir
            and args.project_dir.exists()
            and (not args.project_dir.is_dir() or any(args.project_dir.iterdir()))
        ):
            parser.error("--project-dir must be new or empty")
        uv = ["uv", "run", "--locked", "--all-extras"]
        if args.run_tests or args.browser_tests:
            environment = dict(os.environ)
            if args.browser_tests:
                environment["HIRAKU_TEST_BROWSER"] = args.browser_tests
            print("Running dashboard regression tests…", flush=True)
            subprocess.run(
                [
                    *uv,
                    "pytest",
                    "tests/dashboard",
                    "tests/test_dashboard_demo.py",
                    "-n",
                    "0",
                ],
                cwd=ROOT,
                env=environment,
                check=True,
            )
            subprocess.run(
                [*uv, "marimo", "check", "src/hiraku/dashboard/app.py"],
                cwd=ROOT,
                check=True,
            )
        if not args.no_serve:
            check_port(args.port)
            if args.reopen:
                marker = args.reopen.expanduser().resolve() / MARKER
                _digits = marker.is_file() and marker.read_text() == DIGITS_MARKER
            else:
                _digits = args.example == "digits-mlflow"
            if _digits:
                if args.port == args.mlflow_port:
                    parser.error("--port and --mlflow-port must be different")
                check_port(args.mlflow_port)
        if args.reopen:
            directory, initial_record, has_mlflow = reopen_demo(
                args.reopen, mlflow_port=args.mlflow_port
            )
        elif args.example == "digits-mlflow":
            directory = create_digits_demo(
                args.project_dir,
                epochs=args.epochs,
                mlflow_port=args.mlflow_port,
            )
            initial_record = "digits"
            has_mlflow = True
            print("Dashboard opens in subtree scope to explain the hierarchy.")
            print(
                "Use val/log_loss for Progress; "
                "val/log_loss vs val/accuracy for Pareto."
            )
        else:
            directory = create_demo(args.project_dir)
            initial_record = "child-a"
            has_mlflow = False
            print("Start with child-a: 2 record runs, 3 source runs.", flush=True)
            print("child-b reuses run ID a-1; empty has no runs.", flush=True)
        print(f"Demo project: {directory}", flush=True)
        print("See scripts/README-dashboard.md for the walkthrough.", flush=True)
        if args.no_serve:
            return 0
        return serve_demo(
            directory,
            record_id=initial_record,
            port=args.port,
            mlflow_port=args.mlflow_port if has_mlflow else None,
            no_browser=args.no_browser,
            interactive=args.interactive,
        )
    except KeyboardInterrupt:
        print("\nStopped. Demo files are retained for inspection.")
        return 130
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    except subprocess.CalledProcessError as exc:
        print(f"Command failed with exit code {exc.returncode}.")
        return exc.returncode if exc.returncode > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
