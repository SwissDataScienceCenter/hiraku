"""Serve the bundled marimo dashboard or a project's editable copy."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from importlib.util import find_spec
from pathlib import Path

from ..config import HirakuConfig
from ..errors import ValidationError
from ..record import load_record

DEFAULT_PORT = 8765
DEFAULT_HOST = "127.0.0.1"
BUNDLED_NOTEBOOK = Path(__file__).with_name("app.py")


def project_notebook(config: HirakuConfig) -> Path:
    """Return where ``hiraku dashboard --init`` places the project's notebook."""
    return config.hiraku_dir / "dashboard" / "app.py"


def init_dashboard(config: HirakuConfig) -> Path:
    """Copy the bundled notebook into the project so it can be edited.

    Raises:
        hiraku.errors.ValidationError: If the project already has a dashboard.
    """
    notebook = project_notebook(config)
    if notebook.parent.exists():
        raise ValidationError(f"Dashboard already exists: {notebook.parent}")
    notebook.parent.mkdir(parents=True)
    shutil.copy2(BUNDLED_NOTEBOOK, notebook)
    # marimo keeps session caches in __marimo__/ beside the notebook.
    (notebook.parent / ".gitignore").write_text("__marimo__/\n", encoding="utf-8")
    return notebook


def build_command(
    python: str,
    notebook: Path,
    *,
    port: int = DEFAULT_PORT,
    no_browser: bool = False,
    edit: bool = False,
    include_code: bool = False,
) -> list[str]:
    """Return the marimo command serving ``notebook`` on localhost.

    The read-only app runs without a token; the editor executes arbitrary code,
    so it keeps marimo's access token. ``include_code`` only affects the app,
    since the editor always shows the code.
    """
    command = [python, "-m", "marimo", "edit" if edit else "run"]
    command.extend(["--host", DEFAULT_HOST, "--port", str(port)])
    if edit:
        command.append("--watch")
    else:
        command.append("--no-token")
        if include_code:
            command.append("--include-code")
    if no_browser:
        command.append("--headless")
    command.append(str(notebook))
    return command


def serve_dashboard(
    config: HirakuConfig,
    *,
    record_id: str | None = None,
    port: int = DEFAULT_PORT,
    no_browser: bool = False,
    edit: bool = False,
    include_code: bool = False,
) -> int:
    """Serve the dashboard until interrupted and return marimo's exit code.

    The project's notebook from ``init_dashboard`` is served when it exists,
    otherwise the bundled one.

    Raises:
        hiraku.errors.ValidationError: If the dashboard extra is missing, or if
            ``edit`` is requested for a project without its own notebook.
        hiraku.errors.RecordNotFoundError: If ``record_id`` does not exist.
    """
    local = project_notebook(config)
    if edit and not local.is_file():
        # The bundled notebook is installed package data; edits would be lost.
        raise ValidationError(
            "This project has no dashboard to edit. "
            "Create one with: hiraku dashboard --init"
        )
    if record_id is not None:
        load_record(record_id, config=config)
    if any(find_spec(name) is None for name in ("marimo", "altair")):
        raise ValidationError(
            "The dashboard requires the optional 'dashboard' dependencies. "
            'Install them with: uv sync --extra dashboard (or "hiraku[dashboard]")'
        )
    environment = {
        **os.environ,
        "HIRAKU_PROJECT_ROOT": str(config.root),
        "HIRAKU_DASHBOARD_RECORD": record_id or "",
    }

    def serve(notebook: Path) -> int:
        command = build_command(
            sys.executable,
            notebook,
            port=port,
            no_browser=no_browser,
            edit=edit,
            include_code=include_code,
        )
        return _wait(subprocess.Popen(command, cwd=notebook.parent, env=environment))

    if local.is_file():
        return serve(local)
    # marimo writes session caches beside the notebook and in its working
    # directory, so run a copy from a scratch directory outside the project.
    with tempfile.TemporaryDirectory(prefix="hiraku-dashboard-") as directory:
        return serve(Path(shutil.copy2(BUNDLED_NOTEBOOK, directory)))


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
