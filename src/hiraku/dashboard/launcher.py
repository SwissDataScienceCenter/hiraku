"""Launch the packaged marimo dashboard in application mode."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
from contextlib import nullcontext
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from ..config import HirakuConfig
from ..errors import RecordNotFoundError, ValidationError
from .data import read_record_index
from .supervisor import supervise

DEFAULT_PORT = 8765
DEFAULT_HOST = "127.0.0.1"


def _marimo_available() -> bool:
    return all(find_spec(name) is not None for name in ("marimo", "altair", "pandas"))


def _dashboard_install_hint() -> str:
    return (
        "Install dashboard dependencies with: uv sync --extra dashboard "
        '(or uv tool install "hiraku[dashboard]" for an installed CLI)'
    )


def default_notebook_path() -> Path:
    """Return the bundled notebook shipped inside the Python package."""
    return Path(__file__).with_name("app.py")


def build_command(
    python: str,
    notebook: Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    no_browser: bool = False,
    interactive: bool = False,
    token_password_file: Path | None = None,
) -> list[str]:
    """Build the argument-list marimo command used to serve the notebook.

    Default mode is read-only and uses marimo's ``--no-token`` startup.
    Interactive mode enables marimo's token authentication instead so actions
    are protected by the server's session-based auth.
    """
    command = [python, "-m", "marimo", "run"]
    command.extend(["--host", host, "--port", str(port)])
    command.append("--token" if interactive else "--no-token")
    if interactive and token_password_file is not None:
        command.extend(["--token-password-file", str(token_password_file)])
    if no_browser:
        command.append("--headless")
    command.append(str(notebook))
    return command


def dashboard_environment(
    config: HirakuConfig,
    *,
    record_id: str | None,
    scope: str = "record",
    interactive: bool = False,
) -> dict[str, str]:
    """Return environment variables that preserve project context.

    The interactive flag is forwarded explicitly (``1`` or ``0``) so an
    inherited environment value cannot enable writes accidentally.
    """
    environment = dict(os.environ)
    environment.pop("HIRAKU_DASHBOARD_CONTROLLER_PORT", None)
    environment.pop("HIRAKU_DASHBOARD_CONTROLLER_KEY", None)
    environment["HIRAKU_PROJECT_ROOT"] = str(config.root)
    environment["HIRAKU_DASHBOARD_RECORD"] = record_id or ""
    environment["HIRAKU_DASHBOARD_SCOPE"] = scope
    environment["HIRAKU_DASHBOARD_INTERACTIVE"] = "1" if interactive else "0"
    if not interactive:
        environment.pop("HIRAKU_DASHBOARD_TOKEN_FILE", None)
    return environment


def validate_selection(config: HirakuConfig, record_id: str | None) -> None:
    """Validate a requested record without mutating the project."""
    if record_id is None:
        return
    index, _ = read_record_index(config)
    if record_id not in index:
        raise RecordNotFoundError(f"Record {record_id!r} not found")


def _stop_process(process: subprocess.Popen[Any], *, graceful: bool = False) -> None:
    if process.poll() is not None:
        return
    if graceful and os.name == "posix":
        # Interactive mode owns background workers in the notebook process.
        # SIGINT lets marimo run its shutdown path (and our atexit drain)
        # before the launcher falls back to termination.
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            pass
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def serve_dashboard(
    config: HirakuConfig,
    *,
    record_id: str | None = None,
    scope: str = "record",
    port: int = DEFAULT_PORT,
    no_browser: bool = False,
    notebook: Path | None = None,
    interactive: bool = False,
) -> int:
    """Serve the packaged dashboard and return the marimo exit code.

    The marimo child process uses a temporary working directory so its
    session/cache files never land in the research records. The child is
    terminated when this function exits or receives Ctrl-C.
    """
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValidationError("Port must be an integer between 1 and 65535")
    if scope not in {"record", "source", "subtree"}:
        raise ValidationError("Scope must be 'record', 'source', or 'subtree'")
    if interactive and notebook is not None:
        raise ValidationError(
            "Custom notebooks cannot be combined with --interactive yet."
        )
    validate_selection(config, record_id)
    if not _marimo_available():
        raise ValidationError(
            "The dashboard requires the optional 'dashboard' dependencies. "
            + _dashboard_install_hint()
        )
    notebook_path = (notebook or default_notebook_path()).expanduser().absolute()
    if not notebook_path.is_file():
        raise ValidationError(f"Dashboard notebook not found: {notebook_path}")
    environment = dashboard_environment(
        config,
        record_id=record_id,
        scope=scope,
        interactive=interactive,
    )
    with tempfile.TemporaryDirectory(prefix="hiraku-dashboard-") as directory:
        temporary_dir = Path(directory)
        if notebook is None:
            # Marimo also stores files beside the notebook, not just in cwd.
            notebook_path = Path(shutil.copy2(notebook_path, temporary_dir / "app.py"))
        command = build_command(
            sys.executable,
            notebook_path,
            port=port,
            no_browser=no_browser,
            interactive=interactive,
            token_password_file=(
                Path(environment["HIRAKU_DASHBOARD_TOKEN_FILE"])
                if interactive and environment.get("HIRAKU_DASHBOARD_TOKEN_FILE")
                else None
            ),
        )
        with supervise(config, environment) if interactive else nullcontext():
            process = subprocess.Popen(command, cwd=temporary_dir, env=environment)
            try:
                return process.wait()
            finally:
                _stop_process(process)
