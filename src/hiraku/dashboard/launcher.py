"""Serve the bundled marimo dashboard."""

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


def build_command(
    python: str,
    notebook: Path,
    *,
    port: int = DEFAULT_PORT,
    no_browser: bool = False,
) -> list[str]:
    """Return the ``marimo run`` command serving ``notebook`` on localhost."""
    command = [python, "-m", "marimo", "run", "--host", DEFAULT_HOST]
    command.extend(["--port", str(port), "--no-token"])
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
) -> int:
    """Serve the dashboard until interrupted and return marimo's exit code.

    Raises:
        hiraku.errors.ValidationError: If the dashboard extra is missing.
        hiraku.errors.RecordNotFoundError: If ``record_id`` does not exist.
    """
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
    # marimo writes session caches beside the notebook and in its working
    # directory, so run a copy from a scratch directory outside the project.
    with tempfile.TemporaryDirectory(prefix="hiraku-dashboard-") as directory:
        notebook = Path(shutil.copy2(Path(__file__).with_name("app.py"), directory))
        command = build_command(
            sys.executable, notebook, port=port, no_browser=no_browser
        )
        process = subprocess.Popen(command, cwd=directory, env=environment)
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
