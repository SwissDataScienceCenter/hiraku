from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import ExitStack, contextmanager
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def disable_git_commit_signing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable commit signing in Git subprocesses started by tests."""
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "commit.gpgsign")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "false")


@pytest.fixture
def mlflow_server_factory(tmp_path_factory):
    with ExitStack() as stack:

        def serve(database: Path | None = None) -> str:
            directory = tmp_path_factory.mktemp("mlflow-server")
            return stack.enter_context(
                _serve_mlflow(directory, database or directory / "mlflow.db")
            )

        yield serve


@pytest.fixture
def mlflow_server(mlflow_server_factory):
    return mlflow_server_factory()


@contextmanager
def _serve_mlflow(directory: Path, database: Path):
    """Serve a disposable MLflow store for integration tests over loopback."""
    pytest.importorskip("mlflow")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    uri = f"http://127.0.0.1:{port}"
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("MLFLOW_")
    }
    log = directory / "server.log"
    with log.open("w") as output:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "mlflow",
                "server",
                "--backend-store-uri",
                f"sqlite:///{database}",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--workers",
                "1",
            ],
            cwd=directory,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and process.poll() is None:
            try:
                with urllib.request.urlopen(uri + "/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.1)
        else:
            pytest.fail(f"MLflow server failed to start:\n{log.read_text()}")
        yield uri
    finally:
        if process.poll() is None:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait(timeout=15)
