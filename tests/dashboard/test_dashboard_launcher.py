from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.dashboard import launcher
from hiraku.errors import RecordNotFoundError, ValidationError


def test_build_command_serves_localhost_without_token() -> None:
    command = launcher.build_command("python", Path("app.py"), port=9000)

    assert command == [
        "python",
        "-m",
        "marimo",
        "run",
        "--host",
        "127.0.0.1",
        "--port",
        "9000",
        "--no-token",
        "app.py",
    ]
    headless = launcher.build_command("python", Path("app.py"), no_browser=True)
    assert headless[-2:] == ["--headless", "app.py"]


def test_serve_dashboard_runs_a_copy_outside_the_project(
    project: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {}

    class Process:
        def __init__(self, command, *, cwd, env):
            notebook = Path(command[-1])
            calls.update(
                command=command,
                cwd=cwd,
                env=env,
                copied=notebook.read_text(encoding="utf-8"),
                parent=notebook.parent,
            )

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(subprocess, "Popen", Process)

    assert launcher.serve_dashboard(project, record_id="a", port=9000) == 0

    assert str(calls["parent"]) == calls["cwd"]
    assert not Path(calls["cwd"]).is_relative_to(project.root)
    assert calls["copied"].startswith("# /// script")
    assert calls["env"]["HIRAKU_PROJECT_ROOT"] == str(project.root)
    assert calls["env"]["HIRAKU_DASHBOARD_RECORD"] == "a"
    assert "9000" in calls["command"]


def test_serve_dashboard_rejects_unknown_record(project: HirakuConfig) -> None:
    with pytest.raises(RecordNotFoundError):
        launcher.serve_dashboard(project, record_id="missing")


def test_serve_dashboard_explains_missing_extra(
    project: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(launcher, "find_spec", lambda name: None)

    with pytest.raises(ValidationError, match="uv sync --extra dashboard"):
        launcher.serve_dashboard(project)


def test_dashboard_cli_forwards_options(
    project: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    received = {}

    def serve(config, **options):
        received.update(options, root=config.root)
        return 0

    monkeypatch.setattr(launcher, "serve_dashboard", serve)
    monkeypatch.chdir(project.root)

    result = CliRunner().invoke(
        main, ["dashboard", "--record", "a", "--port", "9000", "--no-browser"]
    )

    assert result.exit_code == 0, result.output
    assert received == {
        "record_id": "a",
        "port": 9000,
        "no_browser": True,
        "root": project.root,
    }


def test_dashboard_cli_propagates_server_exit_code(
    project: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(launcher, "serve_dashboard", lambda config, **options: 3)
    monkeypatch.chdir(project.root)

    assert CliRunner().invoke(main, ["dashboard"]).exit_code == 3
