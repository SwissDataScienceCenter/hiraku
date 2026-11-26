"""Discover, create, and serve named Marimo dashboards."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from importlib.resources import files
from importlib.resources.abc import Traversable
from importlib.util import find_spec
from pathlib import Path
from typing import Literal

from ..errors import ValidationError
from ..project import Project
from ..record import validate_identifier
from ..tracking import Leaderboard

DEFAULT_HOST = "127.0.0.1"
TEMPLATES = files("hiraku") / "resources" / "dashboards"


def _copy_resources(source: Traversable, destination: Path) -> None:
    """Copy a resource tree, including on Python 3.11 without directory as_file."""
    destination.mkdir(parents=True)
    for item in source.iterdir():
        target = destination / item.name
        if item.is_dir():
            if item.name != "__pycache__":
                _copy_resources(item, target)
        elif item.is_file():
            target.write_bytes(item.read_bytes())


def project_notebook(project: Project, name: str = "overview") -> Path:
    """Return a named dashboard's destination in the project."""
    validate_identifier(name, label="dashboard name")
    path = project.hiraku_dir / "dashboards" / f"{name}.py"
    if not path.resolve().is_relative_to(project.hiraku_dir.resolve()):
        raise ValidationError(f"Dashboard path is outside the project: {path}")
    return path


def list_dashboards(project: Project) -> dict[str, Path]:
    """Discover top-level notebooks in the project's dashboards directory."""
    return {
        path.stem: path
        for path in sorted((project.hiraku_dir / "dashboards").glob("*.py"))
        if path.is_file()
    }


def init_dashboard(
    project: Project,
    name: str = "overview",
    *,
    template: Literal["dashboard", "notebook"] = "dashboard",
) -> Path:
    """Create a named project notebook and its independent Studio views.

    Raises:
        hiraku.errors.ValidationError: If the name or destination already exists.
    """
    if template not in {"dashboard", "notebook"}:
        raise ValidationError(f"Unknown dashboard template: {template}")
    notebook = project_notebook(project, name)
    views = notebook.parent / "studio" / name
    if name in list_dashboards(project) or notebook.exists() or views.exists():
        raise ValidationError(f"Dashboard already exists: {name}")
    notebook.parent.mkdir(parents=True, exist_ok=True)
    _copy_bundled(notebook.parent, name, template=template)
    # marimo keeps session caches in __marimo__/ beside the notebook, and
    # marimo-studio keeps build state and locks beside its views.
    ignore = notebook.parent / ".gitignore"
    if not ignore.exists():
        ignore.write_text("__marimo__/\n.locks/\n.artifacts/\n", encoding="utf-8")
    return notebook


def _copy_bundled(
    directory: Path, name: str = "overview", *, template: str = "dashboard"
) -> Path:
    """Copy the bundled notebook and its Studio views into ``directory``."""
    bundle = TEMPLATES / template
    if (bundle / "studio").is_dir():
        _copy_resources(bundle / "studio", directory / "studio" / name)
    notebook = directory / f"{name}.py"
    source = (bundle / "app.py").read_text(encoding="utf-8")
    source = source.replace('view_root = "studio"', f'view_root = "studio/{name}"')
    source = source.replace('css_file="studio/', f'css_file="studio/{name}/')
    notebook.write_text(source, encoding="utf-8")
    return notebook


def build_command(
    python: str,
    notebook: Path,
    *,
    port: int | None = None,
    no_browser: bool = False,
    edit: bool = False,
    include_code: bool = False,
    watch: bool = False,
) -> list[str]:
    """Return the marimo command serving ``notebook`` on localhost.

    The app runs without a token; the notebook editor executes arbitrary code,
    so it keeps marimo's access token. ``include_code`` only affects the app,
    since the editor always shows the code. Without ``port``, marimo selects
    a free port automatically.
    """
    command = [python, "-m", "marimo", "edit" if edit else "run"]
    command.extend(["--host", DEFAULT_HOST])
    if port is not None:
        command.extend(["--port", str(port)])
    if edit or watch:
        command.append("--watch")
    if not edit:
        command.append("--no-token")
        if include_code:
            command.append("--include-code")
    if no_browser:
        command.append("--headless")
    command.append(str(notebook))
    return command


def serve_dashboard(
    project: Project,
    *,
    name: str | None = None,
    record_id: str | None = None,
    leaderboard_name: str | None = None,
    port: int | None = None,
    no_browser: bool = False,
    edit: bool = False,
    include_code: bool = False,
    watch: bool = False,
) -> int:
    """Serve the dashboard until interrupted and return marimo's exit code.

    Select a notebook by name, or use the sole project notebook. With none,
    serve a temporary copy of the bundled dashboard.

    Raises:
        hiraku.errors.ValidationError: If the dashboard extra is missing, or if
            ``edit`` is requested for a project without its own notebook.
        hiraku.errors.RecordNotFoundError: If ``record_id`` does not exist.
    """
    dashboards = list_dashboards(project)
    if name is not None:
        project_notebook(project, name)
        if name not in dashboards:
            raise ValidationError(f"Dashboard not found: {name}")
        local = dashboards[name]
    elif len(dashboards) > 1:
        raise ValidationError(
            "Choose a dashboard: "
            + ", ".join(sorted(dashboards))
            + ". Use: hiraku dashboard serve NAME"
        )
    else:
        local = next(iter(dashboards.values()), None)
    if edit and local is None:
        # The bundled notebook is installed package data; edits would be lost.
        raise ValidationError(
            "This project has no dashboard to edit. "
            "Create one with: hiraku dashboard create overview"
        )
    if record_id is not None:
        project.vault.load(record_id)
    if leaderboard_name is not None:
        Leaderboard.load(project, leaderboard_name)
    if any(find_spec(name) is None for name in ("marimo", "marimo_studio", "altair")):
        raise ValidationError(
            "The dashboard requires the optional 'dashboard' dependencies. "
            'Install them with: uv sync --extra dashboard (or "hiraku[dashboard]")'
        )
    environment = {
        **os.environ,
        "HIRAKU_PROJECT_ROOT": str(project.root),
        "HIRAKU_DASHBOARD_RECORD": record_id or "",
        "HIRAKU_DASHBOARD_LEADERBOARD": leaderboard_name or "",
    }

    def serve(notebook: Path) -> int:
        command = build_command(
            sys.executable,
            notebook,
            port=port,
            no_browser=no_browser,
            edit=edit,
            include_code=include_code,
            watch=watch,
        )
        return _wait(subprocess.Popen(command, cwd=notebook.parent, env=environment))

    if local is not None:
        return serve(local)
    # marimo writes session caches beside the notebook and in its working
    # directory, so run a copy from a scratch directory outside the project.
    with tempfile.TemporaryDirectory(prefix="hiraku-dashboard-") as directory:
        return serve(_copy_bundled(Path(directory)))


def _wait(process: subprocess.Popen[bytes]) -> int:
    try:
        return process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
