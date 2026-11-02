from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig
from hiraku.errors import ValidationError
from hiraku.execution import execute_command
from hiraku.record import Record


def test_run_executes_in_project_and_returns_output_and_exit_code(
    tmp_path: Path,
) -> None:
    config = HirakuConfig.init(tmp_path)
    Record.create("root", config=config)
    result = execute_command(
        [sys.executable, "-c", "import os; print(os.getcwd()); raise SystemExit(7)"],
        record_id="root",
        config=config,
        capture_output=True,
    )
    assert result.returncode == 7
    assert result.stdout.strip() == str(tmp_path)
    assert result.stderr == ""
    assert not (tmp_path / ".jobs").exists()


def test_run_allows_dirty_tree_and_supplies_provenance(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    config = HirakuConfig.init(tmp_path)
    Record.create("root", config=config)
    (tmp_path / "uncommitted.txt").write_text("dirty")
    command = [
        sys.executable,
        "-c",
        "import os; print('|'.join((os.environ.get('HIRAKU_RECORD_ID', ''), "
        "os.environ.get('HIRAKU_GIT_STATE', ''), "
        "os.environ.get('HIRAKU_COMMIT', '<missing>'))) ",
    ]
    result = execute_command(
        command, record_id="root", config=config, capture_output=True
    )
    record_id, git_state, commit = result.stdout.strip().split("|")
    assert record_id == "root"
    assert git_state == "dirty"
    assert commit == ""


def test_run_requires_explicit_command_and_streams_output(
    tmp_path: Path, monkeypatch
) -> None:
    config = HirakuConfig.init(tmp_path)
    monkeypatch.chdir(tmp_path)
    Record.create("root", config=config)

    seen: list[tuple[str, ...] | None] = []

    def run(command=None, **kwargs):
        seen.append(tuple(command) if command is not None else None)
        return subprocess.CompletedProcess([], 0, stdout="inherited\n", stderr="")

    monkeypatch.setattr("hiraku.cli.execute_command", run)
    cli = CliRunner().invoke(
        main,
        ["run", "--record", "root", "--", sys.executable, "-c", "print('explicit')"],
    )
    assert cli.exit_code == 0, cli.output
    assert seen == [(sys.executable, "-c", "print('explicit')")]
    missing = CliRunner().invoke(main, ["run", "--record", "root"])
    assert missing.exit_code != 0
    assert "command" in missing.output.lower()


def test_run_rejects_reserved_attribution_environment(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    Record.create("root", config=config)
    with pytest.raises(ValidationError, match="reserved run attribution"):
        execute_command(
            ["echo", "hello"], env={"HIRAKU_RECORD_ID": "other"}, config=config
        )


def test_cli_propagates_nonzero_exit_code(tmp_path: Path, monkeypatch) -> None:
    config = HirakuConfig.init(tmp_path)
    Record.create("root", config=config)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        main,
        [
            "run",
            "--record",
            "root",
            "--",
            sys.executable,
            "-c",
            "raise SystemExit(4)",
        ],
    )
    assert result.exit_code == 4
