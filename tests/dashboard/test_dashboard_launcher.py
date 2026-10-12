from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

import hiraku.dashboard.launcher as launcher
from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.dashboard.capability import DashboardCapability
from hiraku.errors import RecordNotFoundError, ValidationError
from hiraku.record import Record


def test_build_command_forwards_arguments() -> None:
    notebook = Path("/tmp/dashboard/app.py")
    command = launcher.build_command("python3", notebook, port=8765)
    assert command == [
        "python3",
        "-m",
        "marimo",
        "run",
        "--host",
        "127.0.0.1",
        "--port",
        "8765",
        "--no-token",
        str(notebook),
    ]

    headless = launcher.build_command("python3", notebook, port=9000, no_browser=True)
    assert "--headless" in headless
    assert headless[-1] == str(notebook)


def test_build_command_interactive_uses_token_auth() -> None:
    notebook = Path("/tmp/dashboard/app.py")
    command = launcher.build_command("python3", notebook, interactive=True)
    assert "--token" in command
    assert "--no-token" not in command
    password_file = Path("/tmp/dashboard/token")
    configured = launcher.build_command(
        "python3", notebook, interactive=True, token_password_file=password_file
    )
    assert configured[configured.index("--token-password-file") + 1] == str(
        password_file
    )
    assert "--token-password-file" not in launcher.build_command(
        "python3", notebook, token_password_file=password_file
    )


def test_dashboard_environment_forwards_interactive_mode(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    default = launcher.dashboard_environment(config, record_id=None)
    assert default["HIRAKU_DASHBOARD_INTERACTIVE"] == "0"
    environment = launcher.dashboard_environment(
        config, record_id="child", interactive=True
    )
    assert environment["HIRAKU_DASHBOARD_INTERACTIVE"] == "1"


def test_dashboard_environment_overwrites_inherited_interactive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = HirakuConfig.init(tmp_path)
    monkeypatch.setenv("HIRAKU_DASHBOARD_INTERACTIVE", "1")
    environment = launcher.dashboard_environment(config, record_id=None)
    assert environment["HIRAKU_DASHBOARD_INTERACTIVE"] == "0"


def test_capability_disabled_by_default() -> None:
    capability = DashboardCapability()
    assert capability.interactive is False
    with pytest.raises(ValidationError, match="interactive mode"):
        capability.ensure_can_write()
    with pytest.raises(ValidationError, match="interactive mode"):
        capability.ensure_can_launch()


def test_capability_enabled_in_interactive_mode() -> None:
    capability = DashboardCapability(interactive=True)
    capability.ensure_can_write()
    capability.ensure_can_launch()


def test_dashboard_environment_preserves_project_context(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    environment = launcher.dashboard_environment(
        config, record_id="child", scope="source"
    )
    assert environment["HIRAKU_PROJECT_ROOT"] == str(config.root)
    assert environment["HIRAKU_DASHBOARD_RECORD"] == "child"
    assert environment["HIRAKU_DASHBOARD_SCOPE"] == "source"


def test_dashboard_environment_accepts_subtree_scope(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    environment = launcher.dashboard_environment(
        config, record_id="child", scope="subtree"
    )
    assert environment["HIRAKU_DASHBOARD_SCOPE"] == "subtree"


def test_validate_selection_uses_read_only_index(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    Record.create("root", config=config, activate=False)
    launcher.validate_selection(config, "root")
    with pytest.raises(RecordNotFoundError):
        launcher.validate_selection(config, "missing")


def test_serve_dashboard_reports_missing_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = HirakuConfig.init(tmp_path)
    monkeypatch.setattr(launcher, "find_spec", lambda name: None)
    with pytest.raises(ValidationError, match="uv sync --extra dashboard"):
        launcher.serve_dashboard(config)
    monkeypatch.setattr(launcher, "find_spec", lambda name: object())
    with pytest.raises(ValidationError, match="Port"):
        launcher.serve_dashboard(config, port=0)
    with pytest.raises(ValidationError, match="Scope"):
        launcher.serve_dashboard(config, scope="invalid")


def test_serve_dashboard_rejects_interactive_custom_notebook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = HirakuConfig.init(tmp_path)
    monkeypatch.setattr(launcher, "find_spec", lambda name: object())
    notebook = tmp_path / "custom.py"
    notebook.write_text("import marimo\napp = marimo.App()\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="--interactive"):
        launcher.serve_dashboard(config, notebook=notebook, interactive=True)


def test_cli_dashboard_help_shows_interactive_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    HirakuConfig.init(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["dashboard", "--help"])
    assert result.exit_code == 0
    assert "--interactive" in result.output


def test_cli_dashboard_prints_install_hint_without_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    HirakuConfig.init(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(launcher, "find_spec", lambda name: None)

    result = CliRunner().invoke(main, ["dashboard"], catch_exceptions=False)

    assert result.exit_code != 0
    assert "uv sync --extra dashboard" in result.output


def test_cli_dashboard_accepts_subtree_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    HirakuConfig.init(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(launcher, "find_spec", lambda name: None)

    result = CliRunner().invoke(
        main, ["dashboard", "--scope", "subtree"], catch_exceptions=False
    )

    assert result.exit_code != 0
    assert "uv sync --extra dashboard" in result.output


def test_cli_dashboard_validates_record_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    HirakuConfig.init(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(launcher, "find_spec", lambda name: None)

    result = CliRunner().invoke(
        main, ["dashboard", "--record", "missing"], catch_exceptions=False
    )

    assert result.exit_code != 0
    assert "not found" in result.output


def test_serve_dashboard_forwards_custom_notebook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = HirakuConfig.init(tmp_path)
    Record.create("root", config=config, activate=False)
    custom = tmp_path / "custom.py"
    custom.write_text("import marimo\napp = marimo.App()\n", encoding="utf-8")

    started: list[tuple[list[str], Path, dict[str, str]]] = []

    class FakeProcess:
        def poll(self) -> int | None:
            return 0

        def wait(self, timeout: int | None = None) -> int:
            return 0

        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

    def fake_popen(command, cwd, env):
        started.append((command, Path(cwd), env))
        return FakeProcess()

    monkeypatch.setattr(launcher, "find_spec", lambda name: object())
    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)

    assert launcher.serve_dashboard(config, notebook=custom, scope="source") == 0

    command, cwd, env = started[0]
    assert str(custom) in command
    assert env["HIRAKU_PROJECT_ROOT"] == str(config.root)
    assert env["HIRAKU_DASHBOARD_SCOPE"] == "source"
    assert cwd != config.root


def test_default_notebook_copy_is_cleaned_up_on_interrupt(tmp_path, monkeypatch):
    config = HirakuConfig.init(tmp_path, install_skill=False)
    seen = {}

    class Process:
        stopped = False

        def poll(self):
            return None

        def wait(self, timeout=None):
            if timeout is None:
                raise KeyboardInterrupt
            assert self.stopped
            return 0

        def terminate(self):
            self.stopped = True

    process = Process()

    def start(command, cwd, env):
        copied = Path(command[-1])
        assert copied.parent == cwd
        assert copied != launcher.default_notebook_path()
        assert copied.read_bytes() == launcher.default_notebook_path().read_bytes()
        seen["path"] = copied
        return process

    monkeypatch.setattr(launcher.subprocess, "Popen", start)
    with pytest.raises(KeyboardInterrupt):
        launcher.serve_dashboard(config)
    assert process.stopped
    assert not seen["path"].parent.exists()


def test_shutdown_kills_child_that_does_not_terminate():
    class Process:
        killed = False

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            if not self.killed:
                raise subprocess.TimeoutExpired("marimo", timeout)

        def kill(self):
            self.killed = True

    process = Process()
    launcher._stop_process(process)
    assert process.killed


def test_interactive_interrupt_drains_launcher_owned_worker(tmp_path, monkeypatch):
    from hiraku.dashboard.jobs import LaunchRequest
    from hiraku.dashboard.supervisor import connect_controller

    config = HirakuConfig.init(tmp_path, install_skill=False)
    marker = tmp_path / "finished.txt"
    Record.create(
        "root",
        entrypoints={
            "run": {
                "command": [
                    sys.executable,
                    "-c",
                    "import time; from pathlib import Path; time.sleep(0.2); "
                    "Path('finished.txt').write_text('done')",
                ]
            }
        },
        activate=False,
        config=config,
    )
    real_popen = subprocess.Popen

    class Process:
        stopped = False

        def poll(self):
            return 0 if self.stopped else None

        def wait(self, timeout=None):
            if timeout is None:
                raise KeyboardInterrupt
            return 0

        def terminate(self):
            self.stopped = True

    def start(command, **kwargs):
        if "hiraku.dashboard.worker" in command:
            return real_popen(command, **kwargs)
        for key, value in kwargs["env"].items():
            if key.startswith("HIRAKU_DASHBOARD_CONTROLLER_"):
                monkeypatch.setenv(key, value)
        client = connect_controller()
        client.submit(LaunchRequest("interrupt", "root", "run", {}))
        return Process()

    monkeypatch.setattr(launcher.subprocess, "Popen", start)
    with pytest.raises(KeyboardInterrupt):
        launcher.serve_dashboard(config, interactive=True)
    assert marker.read_text() == "done"


def test_occupied_port_reports_failure(tmp_path):
    HirakuConfig.init(tmp_path, install_skill=False)
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "hiraku.cli",
                "dashboard",
                "--no-browser",
                "--port",
                str(occupied.getsockname()[1]),
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=15,
        )
    assert result.returncode != 0
    assert "in use" in (result.stdout + result.stderr).lower()
