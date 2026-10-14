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
    with_code = launcher.build_command("python", Path("app.py"), include_code=True)
    assert with_code[-2:] == ["--include-code", "app.py"]


def test_build_command_edits_with_token_and_watch() -> None:
    command = launcher.build_command(
        "python", Path("app.py"), port=9000, edit=True, include_code=True
    )

    assert command == [
        "python",
        "-m",
        "marimo",
        "edit",
        "--host",
        "127.0.0.1",
        "--port",
        "9000",
        "--watch",
        "app.py",
    ]


def test_init_dashboard_copies_the_notebook(project: HirakuConfig) -> None:
    notebook = launcher.init_dashboard(project)

    assert notebook == project.hiraku_dir / "dashboard" / "app.py"
    assert notebook.read_text(encoding="utf-8") == (
        launcher.BUNDLED_NOTEBOOK.read_text(encoding="utf-8")
    )
    assert (notebook.parent / ".gitignore").read_text() == "__marimo__/\n"
    with pytest.raises(ValidationError, match="already exists"):
        launcher.init_dashboard(project)


@pytest.fixture
def popen(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Record the marimo process instead of starting it."""
    calls: dict = {}

    class Process:
        def __init__(self, command, *, cwd, env):
            notebook = Path(command[-1])
            calls.update(
                command=command,
                cwd=Path(cwd),
                env=env,
                copied=notebook.read_text(encoding="utf-8"),
                notebook=notebook,
            )

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(subprocess, "Popen", Process)
    return calls


def test_serve_dashboard_runs_a_copy_outside_the_project(
    project: HirakuConfig, popen: dict
) -> None:
    assert launcher.serve_dashboard(project, record_id="a", port=9000) == 0

    assert popen["notebook"].parent == popen["cwd"]
    assert not popen["cwd"].is_relative_to(project.root)
    assert popen["copied"].startswith("# /// script")
    assert popen["env"]["HIRAKU_PROJECT_ROOT"] == str(project.root)
    assert popen["env"]["HIRAKU_DASHBOARD_RECORD"] == "a"
    assert "9000" in popen["command"]
    assert "run" in popen["command"]


@pytest.mark.parametrize("edit", [False, True])
def test_serve_dashboard_prefers_the_project_notebook(
    project: HirakuConfig, popen: dict, edit: bool
) -> None:
    notebook = launcher.init_dashboard(project)

    assert launcher.serve_dashboard(project, edit=edit) == 0

    assert popen["notebook"] == notebook
    assert popen["cwd"] == notebook.parent
    assert ("edit" if edit else "run") in popen["command"]


def test_serve_dashboard_edit_requires_init(project: HirakuConfig) -> None:
    with pytest.raises(ValidationError, match="hiraku dashboard --init"):
        launcher.serve_dashboard(project, edit=True)


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
        "edit": False,
        "include_code": False,
        "root": project.root,
    }


def test_dashboard_cli_init_stops_unless_editing(
    project: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    served = []
    monkeypatch.setattr(
        launcher, "serve_dashboard", lambda config, **options: served.append(options)
    )
    monkeypatch.chdir(project.root)
    runner = CliRunner()

    result = runner.invoke(main, ["dashboard", "--init"])
    assert result.exit_code == 0, result.output
    assert "Created dashboard:" in result.output
    assert served == []

    again = runner.invoke(main, ["dashboard", "--init", "--edit"])
    assert again.exit_code != 0
    assert "Dashboard already exists" in again.output
    assert served == []

    edit = runner.invoke(main, ["dashboard", "--edit", "--include-code"])
    assert edit.exit_code == 0, edit.output
    assert served[0]["edit"] is True
    assert served[0]["include_code"] is True


def test_dashboard_cli_init_then_edit(
    project: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    served = []
    monkeypatch.setattr(
        launcher, "serve_dashboard", lambda config, **options: served.append(options)
    )
    monkeypatch.chdir(project.root)

    result = CliRunner().invoke(main, ["dashboard", "--init", "--edit"])

    assert result.exit_code == 0, result.output
    assert launcher.project_notebook(project).is_file()
    assert served[0]["edit"] is True


def test_dashboard_cli_propagates_server_exit_code(
    project: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(launcher, "serve_dashboard", lambda config, **options: 3)
    monkeypatch.chdir(project.root)

    assert CliRunner().invoke(main, ["dashboard"]).exit_code == 3
