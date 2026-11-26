from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.dashboard import launcher
from hiraku.errors import RecordNotFoundError, ValidationError
from hiraku.project import Project

BUNDLED_NOTEBOOK = Path(str(launcher.TEMPLATES / "dashboard" / "app.py"))
BUNDLED_VIEWS = BUNDLED_NOTEBOOK.parent / "studio"


def test_launcher_rejects_unknown_leaderboard_before_starting_server(tmp_path):
    project = Project.create(tmp_path, install_skill=False, install_mcp=False)
    with pytest.raises(ValidationError, match="Cannot read YAML"):
        launcher.serve_dashboard(project, leaderboard_name="missing")


@pytest.mark.parametrize(
    ("options", "flags"),
    [
        ({}, ["run", "--no-token"]),
        ({"watch": True}, ["run", "--watch", "--no-token"]),
        (
            {"no_browser": True, "include_code": True},
            ["run", "--no-token", "--include-code", "--headless"],
        ),
        # The editor runs code, so it keeps marimo's token; it always shows code.
        ({"edit": True, "include_code": True}, ["edit", "--watch"]),
        ({"edit": True, "watch": True}, ["edit", "--watch"]),
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

    app = load_app(str(BUNDLED_NOTEBOOK))
    assert app._config.css_file
    assert (BUNDLED_NOTEBOOK.parent / app._config.css_file).is_file()
    view = (BUNDLED_VIEWS / "dashboard" / "index.html").read_text(encoding="utf-8")
    placed = set(re.findall(r'<marimo-cell name="([^"]+)">', view))
    named = {cell.name for cell in app._cell_manager.cells()}

    assert placed <= set(named)
    assert placed == {
        "project_name",
        "refresh_control",
        "record_details",
        "record_navigation",
        "leaderboard_header",
        "leaderboard_filters",
        "leaderboard_view",
    }
    assert 'role="tablist"' not in view
    assert "workflow_selection" not in named
    assert "workflow_details" not in named
    assert {
        "data_setup",
        "project_data",
        "selection_state",
        "selected_context",
        "leaderboard_plot",
        "leaderboard_table",
    } <= named


def test_init_dashboard_copies_the_notebook(project: Project) -> None:
    notebook = launcher.init_dashboard(project)

    assert notebook == project.hiraku_dir / "dashboards" / "overview.py"
    assert notebook.read_text(encoding="utf-8") == (
        BUNDLED_NOTEBOOK.read_text(encoding="utf-8")
        .replace('view_root = "studio"', 'view_root = "studio/overview"')
        .replace('css_file="studio/', 'css_file="studio/overview/')
    )
    assert (
        notebook.parent / "studio/overview/dashboard/components.css"
    ).read_bytes() == (BUNDLED_VIEWS / "dashboard/components.css").read_bytes()
    view = notebook.parent / "studio" / "overview" / "dashboard" / "index.html"
    assert view.read_text(encoding="utf-8") == (
        (BUNDLED_VIEWS / "dashboard" / "index.html").read_text(encoding="utf-8")
    )
    for logo in ("sdsc-logo.svg", "sdsc-logo-white.svg"):
        relative = Path("dashboard") / "assets" / logo
        assert (notebook.parent / "studio" / "overview" / relative).read_bytes() == (
            BUNDLED_VIEWS / relative
        ).read_bytes()
    assert (notebook.parent / ".gitignore").read_text() == (
        "__marimo__/\n.locks/\n.artifacts/\n"
    )
    with pytest.raises(ValidationError, match="already exists"):
        launcher.init_dashboard(project)


def test_initialized_studio_view_builds(project: Project) -> None:
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
                view=(
                    notebook.parent / "studio" / notebook.stem / "dashboard/view.toml"
                ).is_file(),
            )

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(subprocess, "Popen", Process)
    return calls


def test_serve_dashboard_runs_a_copy_outside_the_project(
    project: Project, popen: dict
) -> None:
    assert launcher.serve_dashboard(project, record_id="root/a/a", port=9000) == 0

    assert popen["notebook"].parent == popen["cwd"]
    assert not popen["cwd"].is_relative_to(project.root)
    assert popen["copied"].startswith("# /// script")
    assert popen["view"]
    assert popen["env"]["HIRAKU_PROJECT_ROOT"] == str(project.root)
    assert popen["env"]["HIRAKU_DASHBOARD_RECORD"] == "root/a/a"
    assert "9000" in popen["command"]
    assert "run" in popen["command"]


@pytest.mark.parametrize("edit", [False, True])
def test_serve_dashboard_prefers_the_project_notebook(
    project: Project, popen: dict, edit: bool
) -> None:
    notebook = launcher.init_dashboard(project)

    assert launcher.serve_dashboard(project, edit=edit) == 0

    assert popen["notebook"] == notebook
    assert popen["cwd"] == notebook.parent
    assert ("edit" if edit else "run") in popen["command"]


def test_serve_dashboard_edit_requires_init(project: Project) -> None:
    with pytest.raises(ValidationError, match="hiraku dashboard create overview"):
        launcher.serve_dashboard(project, edit=True)


def test_serve_dashboard_rejects_unknown_record(project: Project) -> None:
    with pytest.raises(RecordNotFoundError):
        launcher.serve_dashboard(project, record_id="missing")


def test_serve_dashboard_explains_missing_extra(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(launcher, "find_spec", lambda name: None)

    with pytest.raises(ValidationError, match="uv sync --extra dashboard"):
        launcher.serve_dashboard(project)


def test_dashboard_cli_forwards_options(
    project: Project, monkeypatch: pytest.MonkeyPatch
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
            "serve",
            "--record",
            "a",
            "--port",
            "9000",
            "--no-browser",
            "--include-code",
            "--watch",
        ],
    )

    assert result.exit_code == 0, result.output
    assert received == {
        "name": None,
        "leaderboard_name": None,
        "record_id": "a",
        "port": 9000,
        "no_browser": True,
        "include_code": True,
        "watch": True,
        "root": project.root,
    }


def test_dashboard_cli_requires_subcommand(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    served = []
    monkeypatch.setattr(
        launcher, "serve_dashboard", lambda config, **options: served.append(options)
    )
    monkeypatch.chdir(project.root)
    runner = CliRunner()

    result = runner.invoke(main, ["dashboard"])
    assert result.exit_code == 2
    assert "serve" in result.output
    removed = runner.invoke(main, ["dashboard", "--init"])
    assert removed.exit_code == 2
    assert "No such option" in removed.output
    assert "--init" in removed.output
    assert not served
    assert not launcher.list_dashboards(project)


def test_dashboard_cli_propagates_server_exit_code(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(launcher, "serve_dashboard", lambda config, **options: 3)
    monkeypatch.chdir(project.root)

    assert CliRunner().invoke(main, ["dashboard", "serve"]).exit_code == 3


def test_dashboard_cli_defaults_to_automatic_port_selection(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    received = {}

    def serve(config, **options):
        received.update(options)
        return 0

    monkeypatch.setattr(launcher, "serve_dashboard", serve)
    monkeypatch.chdir(project.root)

    result = CliRunner().invoke(main, ["dashboard", "serve"])

    assert result.exit_code == 0, result.output
    assert received["port"] is None


def test_named_dashboards_have_independent_views(project, popen):
    first = launcher.init_dashboard(project, "exploration")
    second = launcher.init_dashboard(project, "results")
    assert launcher.list_dashboards(project) == {
        "exploration": first,
        "results": second,
    }
    assert (first.parent / "studio/exploration/dashboard/index.html").is_file()
    assert (second.parent / "studio/results/dashboard/index.html").is_file()
    with pytest.raises(ValidationError, match="Choose a dashboard"):
        launcher.serve_dashboard(project)
    launcher.serve_dashboard(project, name="results", edit=True)
    assert popen["notebook"] == second
    assert "edit" in popen["command"]


@pytest.mark.parametrize("name", ["../escape", "nested/name", "", ".", ".."])
def test_dashboard_creation_rejects_invalid_names(project, name):
    with pytest.raises(ValidationError, match="Invalid dashboard name"):
        launcher.init_dashboard(project, name)
    assert not (project.hiraku_dir / "dashboards").exists()


def test_named_dashboard_missing_does_not_start_bundled_fallback(project, popen):
    with pytest.raises(ValidationError, match="Dashboard not found"):
        launcher.serve_dashboard(project, name="missing")
    assert not popen


def test_dashboard_discovery_uses_the_dashboards_directory(project):
    other = project.hiraku_dir / "dashboard" / "app.py"
    other.parent.mkdir()
    other.touch()
    assert launcher.list_dashboards(project) == {}


def test_dashboard_subcommands_create_list_and_serve(project, popen, monkeypatch):
    monkeypatch.chdir(project.root)
    runner = CliRunner()
    empty = runner.invoke(main, ["dashboard", "list"])
    assert empty.exit_code == 0
    assert "No project dashboards" in empty.output
    created = runner.invoke(main, ["dashboard", "create", "exploration"])
    assert created.exit_code == 0, created.output
    listed = runner.invoke(main, ["dashboard", "list"])
    assert listed.exit_code == 0
    assert listed.output == "exploration\n"
    served = runner.invoke(main, ["dashboard", "serve", "--port", "9001", "--watch"])
    assert served.exit_code == 0, served.output
    assert popen["notebook"].name == "exploration.py"
    assert "9001" in popen["command"]
    assert "--watch" in popen["command"]


def test_notebook_template_runs_without_studio(project, popen):
    import runpy

    notebook = launcher.init_dashboard(project, "exploration", template="notebook")
    assert not (notebook.parent / "studio").exists()
    outputs, _ = runpy.run_path(str(notebook))["app"].run()
    assert any(
        "Describe your question" in getattr(output, "text", "") for output in outputs
    )
    launcher.serve_dashboard(project, name="exploration", edit=True)
    assert popen["notebook"] == notebook
    with pytest.raises(ValidationError, match="already exists"):
        launcher.init_dashboard(project, "exploration", template="notebook")


def test_unknown_template_does_not_create_files(project):
    with pytest.raises(ValidationError, match="Unknown dashboard template"):
        launcher.init_dashboard(project, "exploration", template="missing")
    assert not (project.hiraku_dir / "dashboards").exists()


def test_create_cli_selects_notebook_template(project, monkeypatch):
    monkeypatch.chdir(project.root)
    runner = CliRunner()
    result = runner.invoke(
        main, ["dashboard", "create", "exploration", "--template", "notebook"]
    )
    assert result.exit_code == 0, result.output
    notebook = project.hiraku_dir / "dashboards/exploration.py"
    assert notebook.is_file()
    assert not (notebook.parent / "studio").exists()
    invalid = runner.invoke(
        main, ["dashboard", "create", "other", "--template", "missing"]
    )
    assert invalid.exit_code == 2
    assert not (notebook.parent / "other.py").exists()


@pytest.mark.parametrize("name", [None, "minimal"])
def test_dashboard_edit_selects_notebook(project, popen, monkeypatch, name):
    launcher.init_dashboard(project, "minimal", template="notebook")
    monkeypatch.chdir(project.root)
    result = CliRunner().invoke(
        main,
        [
            "dashboard",
            "edit",
            *([name] if name else []),
            "--port",
            "9002",
            "--no-browser",
        ],
    )
    assert result.exit_code == 0, result.output
    assert popen["notebook"].name == "minimal.py"
    assert "edit" in popen["command"]
    assert "--watch" in popen["command"]
    assert "9002" in popen["command"]
    assert "--headless" in popen["command"]


@pytest.mark.parametrize(
    "arguments", [["dashboard", "--edit"], ["dashboard", "serve", "--edit"]]
)
def test_dashboard_rejects_removed_edit_flag(project, monkeypatch, arguments):
    monkeypatch.chdir(project.root)
    result = CliRunner().invoke(main, arguments)
    assert result.exit_code == 2
    assert "No such option" in result.output
    assert "--edit" in result.output
