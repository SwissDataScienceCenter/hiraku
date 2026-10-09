from __future__ import annotations

import csv
import json
import os
import runpy
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import pytest

from hiraku.config import HirakuConfig
from hiraku.dashboard import build_snapshot

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/test_dashboard.py"
helpers = runpy.run_path(str(SCRIPT))


@pytest.mark.parametrize(
    ("options", "expected_checks"),
    [
        ([], 0),
        (["--skip-tests"], 0),
        (["--run-tests"], 2),
        (["--browser-tests", "webkit"], 2),
    ],
)
def test_demo_checks_are_opt_in(monkeypatch, tmp_path, options, expected_checks):
    main = helpers["main"]
    calls = []
    monkeypatch.setattr(
        sys, "argv", [str(SCRIPT), "--example", "csv", "--no-serve", *options]
    )
    monkeypatch.setitem(main.__globals__, "create_demo", lambda path: tmp_path)
    monkeypatch.setattr(subprocess, "run", lambda args, **kwargs: calls.append(args))
    assert main() == 0
    assert len(calls) == expected_checks
    if expected_checks:
        assert "pytest" in calls[0]
        assert "marimo" in calls[1]


def test_demo_has_distinct_scopes_and_refresh_rows(tmp_path):
    directory = helpers["create_demo"](tmp_path / "demo")
    config = HirakuConfig.load(directory / "hiraku.yml")
    assert len(build_snapshot(config, record_id="child-a").runs) == 2
    assert len(build_snapshot(config, record_id="child-a", scope="source").runs) == 3
    assert build_snapshot(config, record_id="empty").runs == ()
    assert not (directory / "results/empty.csv").exists()
    assert config.active_record_id is None
    assert helpers["append_run"](directory) == "refreshed-1"
    assert helpers["append_run"](directory) == "refreshed-2"
    assert len(build_snapshot(config, record_id="child-a").runs) == 4
    with (directory / "results/a.csv").open(newline="") as stream:
        assert len(list(csv.DictReader(stream))) == 5


def test_demo_refuses_to_overwrite_files(tmp_path):
    important = tmp_path / "keep.txt"
    important.write_text("keep")
    with pytest.raises(ValueError, match="must be empty"):
        helpers["create_demo"](tmp_path)
    assert important.read_text() == "keep"


def test_append_refuses_non_demo_project(tmp_path):
    config = HirakuConfig.init(tmp_path, install_skill=False)
    with pytest.raises(ValueError, match="Not a dashboard demo"):
        helpers["append_run"](tmp_path)
    assert config.path.is_file()
    assert not (tmp_path / "results/a.csv").exists()


def test_append_refuses_changed_csv_schema(tmp_path):
    directory = helpers["create_demo"](tmp_path / "demo")
    csv_path = directory / "results/a.csv"
    csv_path.write_text("custom,columns\na,b\n")
    before = csv_path.read_bytes()
    with pytest.raises(ValueError, match="Unexpected demo CSV columns"):
        helpers["append_run"](directory)
    assert csv_path.read_bytes() == before


@pytest.mark.integration
def test_digits_demo_trains_and_exposes_real_runs(tmp_path):
    pytest.importorskip("mlflow")
    pytest.importorskip("sklearn")
    directory = helpers["create_digits_demo"](tmp_path / "digits", epochs=2)
    config = HirakuConfig.load(directory / "hiraku.yml")
    database = directory / "mlflow.db"
    before = database.read_bytes()
    snapshot = build_snapshot(config, record_id="weak-l2", scope="source")
    assert database.read_bytes() == before
    assert len(snapshot.runs) == 2
    assert {run.record_id for run in snapshot.runs} == {"weak-l2", "strong-l2"}
    assert {run.params["epochs"] for run in snapshot.runs} == {"2"}
    assert {run.params["alpha"] for run in snapshot.runs} == {"0.0001", "1.0"}
    assert all(run.entrypoint == "train" and run.commit for run in snapshot.runs)
    assert len(build_snapshot(config, record_id="weak-l2").runs) == 1
    assert build_snapshot(config, record_id="digits").runs == ()
    subtree = build_snapshot(config, record_id="digits", scope="subtree")
    assert len(subtree.runs) == 2
    assert all(run.url and f"/runs/{run.id}" in run.url for run in subtree.runs)
    assert config.active_record_id is None

    helpers["launch_digits_run"](directory, "strong-l2")
    helpers["verify_digits_demo"](directory)
    assert len(build_snapshot(config, record_id="strong-l2").runs) == 2
    assert len(build_snapshot(config, record_id="weak-l2", scope="source").runs) == 3
    with pytest.raises(ValueError, match="requires a CSV demo"):
        helpers["append_run"](directory)


def test_digits_helpers_refuse_existing_or_unmarked_projects(tmp_path):
    important = tmp_path / "keep.txt"
    important.write_text("keep")
    with pytest.raises(ValueError, match="must be empty"):
        helpers["create_digits_demo"](tmp_path)
    with pytest.raises(ValueError, match="Not a Digits dashboard demo"):
        helpers["launch_digits_run"](tmp_path, "weak-l2")
    assert important.read_text() == "keep"
    assert not (tmp_path / "mlflow.db").exists()
    assert not (tmp_path / ".git").exists()


def test_digits_refuses_invalid_epoch_budget(tmp_path):
    directory = tmp_path / "digits"
    with pytest.raises(ValueError, match="epochs must be positive"):
        helpers["create_digits_demo"](directory, epochs=0)
    assert not directory.exists()


def test_servers_reject_conflicting_and_occupied_ports(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        probe.listen()
        port = probe.getsockname()[1]
        with pytest.raises(ValueError, match="different ports"):
            helpers["serve_demo"](
                tmp_path,
                record_id="weak-l2",
                port=port,
                mlflow_port=port,
                no_browser=True,
            )
        with pytest.raises(ValueError, match="unavailable"):
            helpers["serve_demo"](
                tmp_path,
                record_id="child-a",
                port=port,
                mlflow_port=None,
                no_browser=True,
            )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.integration
@pytest.mark.skipif(os.name == "nt", reason="Uses POSIX Ctrl-C signaling")
def test_demo_serves_mlflow_and_dashboard_and_stops_both(tmp_path):
    pytest.importorskip("mlflow")
    pytest.importorskip("sklearn")
    with socket.socket() as dashboard_probe, socket.socket() as mlflow_probe:
        dashboard_probe.bind(("127.0.0.1", 0))
        mlflow_probe.bind(("127.0.0.1", 0))
        port = dashboard_probe.getsockname()[1]
        mlflow_port = mlflow_probe.getsockname()[1]
    dashboard_url = f"http://127.0.0.1:{port}"
    mlflow_url = f"http://127.0.0.1:{mlflow_port}"
    log_path = tmp_path / "demo.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                str(SCRIPT),
                "--skip-tests",
                "--epochs",
                "2",
                "--no-browser",
                "--port",
                str(port),
                "--mlflow-port",
                str(mlflow_port),
                "--project-dir",
                str(tmp_path / "demo"),
            ],
            cwd=SCRIPT.parent.parent,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 90
            while True:
                assert process.poll() is None, log_path.read_text()
                assert time.monotonic() < deadline, log_path.read_text()
                try:
                    with urlopen(dashboard_url, timeout=1):
                        break
                except (URLError, TimeoutError):
                    time.sleep(0.1)
            request = Request(
                f"{mlflow_url}/api/2.0/mlflow/experiments/search",
                data=b'{"max_results": 100}',
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=5) as response:
                experiments = json.load(response)["experiments"]
            experiment = next(e for e in experiments if e["name"] == "hiraku-digits")
            request = Request(
                f"{mlflow_url}/api/2.0/mlflow/runs/search",
                data=json.dumps(
                    {"experiment_ids": [experiment["experiment_id"]]}
                ).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=5) as response:
                runs = json.load(response)["runs"]
            assert len(runs) == 2
            assert all(run["info"]["status"] == "FINISHED" for run in runs)
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                process.wait(timeout=20)
        assert process.returncode == 130, log_path.read_text()
    deadline = time.monotonic() + 10
    while True:
        try:
            helpers["check_port"](port)
            helpers["check_port"](mlflow_port)
            break
        except ValueError:
            assert time.monotonic() < deadline, log_path.read_text()
            time.sleep(0.1)
    output = log_path.read_text()
    assert f"Hiraku dashboard: {dashboard_url}" in output
    assert f"MLflow UI: {mlflow_url}" in output
