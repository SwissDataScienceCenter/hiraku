from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

import hiraku.dashboard.launcher as launcher
from hiraku.cli import main
from hiraku.config import HirakuConfig
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


def test_dashboard_environment_preserves_project_context(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    environment = launcher.dashboard_environment(
        config, record_id="child", scope="source"
    )
    assert environment["HIRAKU_PROJECT_ROOT"] == str(config.root)
    assert environment["HIRAKU_DASHBOARD_RECORD"] == "child"
    assert environment["HIRAKU_DASHBOARD_SCOPE"] == "source"


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


def test_cli_dashboard_prints_install_hint_without_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    HirakuConfig.init(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(launcher, "find_spec", lambda name: None)

    result = CliRunner().invoke(main, ["dashboard"], catch_exceptions=False)

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

    assert launcher.serve_dashboard(config, notebook=custom) == 0

    command, cwd, env = started[0]
    assert str(custom) in command
    assert env["HIRAKU_PROJECT_ROOT"] == str(config.root)
    assert cwd != config.root
