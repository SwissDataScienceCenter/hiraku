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
from hiraku.record import Record, load_record

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


def test_demo_help_advertises_interactive(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--help"])
    with pytest.raises(SystemExit) as exc:
        helpers["main"]()
    assert exc.value.code == 0
    assert "--interactive" in capsys.readouterr().out


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


def test_reopen_retained_digits_updates_only_ui_configuration(tmp_path):
    directory = tmp_path / "digits"
    config = HirakuConfig.init(directory, install_skill=False)
    Record.create(
        "digits",
        config=config,
        activate=False,
        tracking_backend={"type": "mlflow", "database": "mlflow.db"},
    )
    (directory / helpers["MARKER"]).write_text(helpers["DIGITS_MARKER"])
    database = directory / "mlflow.db"
    database.write_bytes(b"retained database")
    assert helpers["reopen_demo"](directory, mlflow_port=5001) == (
        directory.resolve(),
        "digits",
        True,
    )
    assert load_record("digits", config=config).tracking_backend["ui_url"] == (
        "http://127.0.0.1:5001"
    )
    assert database.read_bytes() == b"retained database"
    assert HirakuConfig.load(config.path).active_record_id is None


def test_reopen_csv_is_nonmutating_and_rejects_unmarked_projects(tmp_path):
    directory = helpers["create_demo"](tmp_path / "csv")
    before = {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    assert helpers["reopen_demo"](directory, mlflow_port=5001) == (
        directory.resolve(),
        "child-a",
        False,
    )
    assert {p: p.read_bytes() for p in before} == before
    with pytest.raises(ValueError, match="Not a dashboard demo"):
        helpers["reopen_demo"](tmp_path, mlflow_port=5001)


def test_busy_port_fails_before_training(monkeypatch, tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        probe.listen()
        port = probe.getsockname()[1]
        directory = tmp_path / "demo"
        monkeypatch.setattr(
            sys,
            "argv",
            [str(SCRIPT), "--port", str(port), "--project-dir", str(directory)],
        )
        with pytest.raises(SystemExit) as exc:
            helpers["main"]()
        assert exc.value.code == 1
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


def test_interactive_server_shutdown_sends_interrupt(monkeypatch):
    signals = []

    class Process:
        pid = 123
        args = ["uv", "run", "hiraku", "dashboard", "--interactive"]

        def wait(self, timeout):
            assert timeout == 40
            return 0

    monkeypatch.setattr(os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    helpers["stop_server"](Process())
    assert signals == [(123, signal.SIGINT)]


def test_interactive_digits_server_passes_matching_mlflow_environment(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    serve = helpers["serve_demo"]
    (tmp_path / "mlflow.db").write_bytes(b"demo")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "https://other.example.invalid")
    started = []

    class Process:
        def poll(self):
            return None

    def start(command, **kwargs):
        started.append((command, kwargs["env"]))
        return Process()

    def interrupt(duration):
        raise KeyboardInterrupt

    monkeypatch.setitem(serve.__globals__, "check_port", lambda port: None)
    monkeypatch.setitem(serve.__globals__, "wait_for_server", lambda *args: None)
    monkeypatch.setitem(serve.__globals__, "stop_server", lambda process: None)
    monkeypatch.setitem(serve.__globals__, "mlflow_training_url", lambda url: url)
    monkeypatch.setitem(serve.__globals__, "subprocess", SimpleNamespace(Popen=start))
    monkeypatch.setitem(serve.__globals__, "time", SimpleNamespace(sleep=interrupt))
    with pytest.raises(KeyboardInterrupt):
        serve(
            tmp_path,
            record_id="digits",
            port=8765,
            mlflow_port=5000,
            no_browser=True,
            interactive=True,
        )
    dashboard_command, environment = started[1]
    assert "--interactive" in dashboard_command
    assert environment["MLFLOW_TRACKING_URI"] == f"sqlite:///{tmp_path / 'mlflow.db'}"
    assert environment["MLFLOW_EXPERIMENT_NAME"] == "hiraku-digits"
    assert started[0][1]["MLFLOW_TRACKING_URI"] == "https://other.example.invalid"


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
