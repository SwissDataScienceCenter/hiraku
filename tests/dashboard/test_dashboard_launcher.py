from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.dashboard import launcher
from hiraku.errors import RecordNotFoundError, ValidationError


@pytest.mark.parametrize(
    ("options", "flags"),
    [
        ({}, ["run", "--no-token"]),
        (
            {"no_browser": True, "include_code": True},
            ["run", "--no-token", "--include-code", "--headless"],
        ),
        # The editor runs code, so it keeps marimo's token; it always shows code.
        ({"edit": True, "include_code": True}, ["edit", "--watch"]),
    ],
)
def test_build_command(options: dict, flags: list[str]) -> None:
    command = launcher.build_command("python", Path("app.py"), port=9000, **options)

    mode, *rest = flags
    assert command == [
        "python",
        "-m",
        "marimo",
        mode,
        "--host",
        "127.0.0.1",
        "--port",
        "9000",
        *rest,
        "app.py",
    ]


@pytest.mark.parametrize("edit", [False, True])
def test_build_command_leaves_automatic_port_selection_to_marimo(edit: bool) -> None:
    command = launcher.build_command("python", Path("app.py"), edit=edit)

    assert "--port" not in command
    assert command[3] == ("edit" if edit else "run")
    assert command[command.index("--host") + 1] == "127.0.0.1"


def test_bundled_views_place_all_interactive_cells() -> None:
    from marimo._ast.load import load_app

    app = load_app(str(launcher.BUNDLED_NOTEBOOK))
    assert app._config.css_file
    assert (launcher.BUNDLED_NOTEBOOK.parent / app._config.css_file).is_file()
    view = (launcher.BUNDLED_VIEWS / "dashboard" / "index.html").read_text(
        encoding="utf-8"
    )
    placed = set(re.findall(r'<marimo-cell name="([^"]+)">', view))
    named = {cell.name for cell in app._cell_manager.cells()}

    assert placed <= set(named)
    assert placed == {
        "project_name",
        "refresh_control",
        "run_table",
        "record_details",
        "record_navigation",
        "run_details",
        "metric_analysis",
    }
    assert {
        "data_setup",
        "project_data",
        "selection_state",
        "selected_context",
    } <= named


def test_init_dashboard_copies_the_notebook(project: HirakuConfig) -> None:
    notebook = launcher.init_dashboard(project)

    assert notebook == project.hiraku_dir / "dashboard" / "app.py"
    assert notebook.read_text(encoding="utf-8") == (
        launcher.BUNDLED_NOTEBOOK.read_text(encoding="utf-8")
    )
    assert (notebook.parent / "studio/dashboard/components.css").read_bytes() == (
        launcher.BUNDLED_VIEWS / "dashboard/components.css"
    ).read_bytes()
    view = notebook.parent / "studio" / "dashboard" / "index.html"
    assert view.read_text(encoding="utf-8") == (
        (launcher.BUNDLED_VIEWS / "dashboard" / "index.html").read_text(
            encoding="utf-8"
        )
    )
    for logo in ("sdsc-logo.svg", "sdsc-logo-white.svg"):
        relative = Path("dashboard") / "assets" / logo
        assert (notebook.parent / "studio" / relative).read_bytes() == (
            launcher.BUNDLED_VIEWS / relative
        ).read_bytes()
    assert (notebook.parent / ".gitignore").read_text() == (
        "__marimo__/\nstudio/.locks/\nstudio/*/.artifacts/\n"
    )
    with pytest.raises(ValidationError, match="already exists"):
        launcher.init_dashboard(project)


def test_initialized_studio_view_builds(project: HirakuConfig) -> None:
    pytest.importorskip("marimo_studio")
    from marimo_studio.authoring import open_workspace

    notebook = launcher.init_dashboard(project)
    view = open_workspace(notebook).view("dashboard")

    built = asyncio.run(view.build())

    assert built.revision
    assert not [issue for issue in built.issues if issue.severity == "error"]


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
                view=(notebook.parent / "studio" / "dashboard" / "view.toml").is_file(),
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
    assert popen["view"]
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
        main,
        [
            "dashboard",
            "--record",
            "a",
            "--port",
            "9000",
            "--no-browser",
            "--include-code",
        ],
    )

    assert result.exit_code == 0, result.output
    assert received == {
        "record_id": "a",
        "port": 9000,
        "no_browser": True,
        "edit": False,
        "include_code": True,
        "root": project.root,
    }


@pytest.mark.parametrize("edit", [False, True])
def test_dashboard_cli_init_serves_only_with_edit(
    project: HirakuConfig, monkeypatch: pytest.MonkeyPatch, edit: bool
) -> None:
    served = []
    monkeypatch.setattr(
        launcher, "serve_dashboard", lambda config, **options: served.append(options)
    )
    monkeypatch.chdir(project.root)
    runner = CliRunner()

    result = runner.invoke(main, ["dashboard", "--init", *(["--edit"] if edit else [])])

    assert result.exit_code == 0, result.output
    assert "Created dashboard:" in result.output
    assert [options["edit"] for options in served] == ([True] if edit else [])
    again = runner.invoke(main, ["dashboard", "--init"])
    assert again.exit_code != 0
    assert "Dashboard already exists" in again.output


def test_dashboard_cli_propagates_server_exit_code(
    project: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(launcher, "serve_dashboard", lambda config, **options: 3)
    monkeypatch.chdir(project.root)

    assert CliRunner().invoke(main, ["dashboard"]).exit_code == 3


def test_dashboard_cli_defaults_to_automatic_port_selection(
    project: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    received = {}

    def serve(config, **options):
        received.update(options)
        return 0

    monkeypatch.setattr(launcher, "serve_dashboard", serve)
    monkeypatch.chdir(project.root)

    result = CliRunner().invoke(main, ["dashboard"])

    assert result.exit_code == 0, result.output
    assert received["port"] is None
