"""Check the dashboard, create disposable fixture data, and launch it locally."""

from __future__ import annotations

import argparse
import csv
import os
import runpy
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = ".hiraku-dashboard-demo"


def create_demo(directory: Path | None = None) -> Path:
    """Create a fresh demo, refusing to overwrite an existing project."""
    if directory is None:
        directory = Path(tempfile.mkdtemp(prefix="hiraku-dashboard-demo-"))
    directory = directory.expanduser().resolve()
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        raise ValueError(f"Demo directory must be empty: {directory}")
    directory.mkdir(parents=True, exist_ok=True)
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
    (directory / MARKER).write_text(
        "Hiraku dashboard demo, version 1\n", encoding="utf-8"
    )
    return directory


def append_run(directory: Path) -> str:
    """Append a run only to a demo created by this script."""
    directory = directory.expanduser().resolve()
    if not (directory / MARKER).is_file():
        raise ValueError(f"Not a dashboard demo created by this script: {directory}")
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-dir", type=Path, help="Use a new or empty demo directory"
    )
    parser.add_argument("--port", type=port_number, default=8765)
    parser.add_argument(
        "--no-browser", action="store_true", help="Do not open a browser"
    )
    parser.add_argument(
        "--no-serve", action="store_true", help="Create the demo, then exit"
    )
    parser.add_argument(
        "--skip-tests", action="store_true", help="Skip automated checks"
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
    args = parser.parse_args()
    try:
        if args.append_run:
            identifier = append_run(args.append_run)
            print(f"Added {identifier}. Press Refresh in the dashboard.", flush=True)
            return 0
        if args.browser_tests and args.skip_tests:
            parser.error("--browser-tests cannot be combined with --skip-tests")
        if (
            args.project_dir
            and args.project_dir.exists()
            and (not args.project_dir.is_dir() or any(args.project_dir.iterdir()))
        ):
            parser.error("--project-dir must be new or empty")
        uv = ["uv", "run", "--locked", "--all-extras"]
        if not args.skip_tests:
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
        directory = create_demo(args.project_dir)
        print(f"Demo project: {directory}", flush=True)
        print("Start with child-a: 2 record runs, 3 source runs.", flush=True)
        print("child-b reuses run ID a-1; empty has no runs.", flush=True)
        print("See scripts/README-dashboard.md for the walkthrough.", flush=True)
        if args.no_serve:
            return 0
        print(
            f"Open http://127.0.0.1:{args.port} — Ctrl-C stops the server.", flush=True
        )
        subprocess.run(
            [
                "uv",
                "run",
                "--project",
                str(ROOT),
                "--locked",
                "--all-extras",
                "hiraku",
                "dashboard",
                "--record",
                "child-a",
                "--port",
                str(args.port),
                *(["--no-browser"] if args.no_browser else []),
            ],
            cwd=directory,
            check=True,
        )
        return 0
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
