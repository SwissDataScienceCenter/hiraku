from __future__ import annotations

import importlib
import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from urllib.request import urlopen

import pytest

from hiraku.config import HirakuConfig
from hiraku.dashboard import build_snapshot
from hiraku.dashboard.plots import pareto_points, progress_points
from hiraku.record import load_record

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/use_case_dashboard.py"
sys.path.insert(0, str(SCRIPT.parent))
try:
    _demo = importlib.import_module("use_case_dashboard")
finally:
    sys.path.remove(str(SCRIPT.parent))
create_use_case = _demo.create_use_case
reopen_use_case = _demo.reopen_use_case


@pytest.mark.parametrize(
    ("example", "root", "count"),
    [
        ("minimal", "my-task", 1),
        ("heart-disease", "heart-disease", 2),
        ("autoresearch", "autoresearch", 3),
    ],
)
def test_local_use_cases_log_attributed_runs(tmp_path, example, root, count):
    demo = create_use_case(example, tmp_path / example)
    config = HirakuConfig.load(demo.directory / "hiraku.yml")
    snapshot = build_snapshot(config, record_id=root, scope="subtree")
    assert not snapshot.errors
    assert len(snapshot.runs) == count
    assert all(
        run.record_id != root and run.commit and run.entrypoint for run in snapshot.runs
    )
    assert reopen_use_case(demo.directory, mlflow_port=5000) == demo
    if example == "autoresearch":
        assert config.default_dispatch == "launch.sh"
        progress = progress_points(snapshot.runs, "val_bpb", direction="minimize")
        assert progress.omitted == 0
        assert set(progress.frame["value"]) == {1.0, 0.85, 0.95}
        pareto = pareto_points(snapshot.runs, "val_bpb", "peak_vram_mb")
        assert pareto.omitted == 0
        assert set(pareto.frame["y"]) == {1024.0, 1536.0, 768.0}
        assert pareto.frame["is_pareto"].sum() == 2
        _demo._launch(config, "lower-loss", "mock")
        refreshed = build_snapshot(config, record_id=root, scope="subtree")
        assert not refreshed.errors
        assert len(refreshed.runs) == 4


def test_iris_use_case_prepares_records_without_network_or_credentials(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("WANDB_API_KEY", raising=False)
    monkeypatch.delenv("WANDB_ENTITY", raising=False)
    demo = create_use_case("iris-wandb", tmp_path / "iris")
    config = HirakuConfig.load(demo.directory / "hiraku.yml")
    assert load_record("logistic", config=config).entrypoint("train", config=config)
    assert load_record("random-forest", config=config).entrypoint(
        "train", config=config
    )
    assert not (demo.directory / "runs.csv").exists()
    assert reopen_use_case(demo.directory, mlflow_port=5000) == demo


def test_iris_entity_is_shared_by_initial_and_gui_launches(tmp_path, monkeypatch):
    launched = []
    monkeypatch.setattr(
        _demo,
        "_launch",
        lambda config, record_id, name, *, env: launched.append((record_id, env)),
    )
    demo = create_use_case(
        "iris-wandb", tmp_path / "iris", run_iris=True, wandb_entity="research-team"
    )
    assert {record_id for record_id, _ in launched} == {"logistic", "random-forest"}
    assert all(
        env == {"WANDB_ENTITY": "research-team", "WANDB_PROJECT": "hiraku-iris"}
        for _, env in launched
    )
    served = []
    monkeypatch.setattr(_demo, "check_port", lambda port: None)
    monkeypatch.setattr(
        _demo, "serve_demo", lambda *args, **kwargs: served.append(kwargs) or 0
    )
    assert _demo.main(["--reopen", str(demo.directory), "--no-browser"]) == 0
    assert served[0]["launch_environment"] == {
        "WANDB_ENTITY": "research-team",
        "WANDB_PROJECT": "hiraku-iris",
    }
    assert served[0]["scope"] == "subtree"


def test_demo_requires_an_empty_directory(tmp_path):
    (tmp_path / "keep.txt").write_text("keep")
    with pytest.raises(ValueError, match="must be empty"):
        create_use_case("minimal", tmp_path)
    assert (tmp_path / "keep.txt").read_text() == "keep"


@pytest.mark.integration
@pytest.mark.skipif(os.name == "nt", reason="Uses POSIX Ctrl-C signaling")
def test_interactive_demo_prints_working_token_url(tmp_path):
    demo = create_use_case("minimal", tmp_path / "minimal")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    log_path = tmp_path / "server.log"
    with log_path.open("w+") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                str(SCRIPT),
                "--reopen",
                str(demo.directory),
                "--port",
                str(port),
                "--no-browser",
            ],
            cwd=SCRIPT.parents[1],
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 45
            url_line = None
            while time.monotonic() < deadline:
                log.seek(0)
                output = log.read()
                url_line = next(
                    (
                        line
                        for line in output.splitlines()
                        if line.startswith("Hiraku GUI is now running on: ")
                    ),
                    None,
                )
                if url_line:
                    break
                assert process.poll() is None, "Demo server stopped during startup"
                time.sleep(0.1)
            assert url_line is not None, "Authenticated URL was not printed"
            url = url_line.split(": ", 1)[1]
            assert f"127.0.0.1:{port}" in url
            assert "access_token=" in url
            assert "Tracking backend on: " in output
            assert "Hiraku token is: " in output
            with urlopen(url, timeout=5) as response:
                assert response.status == 200
        finally:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGINT)
            process.wait(timeout=40)
