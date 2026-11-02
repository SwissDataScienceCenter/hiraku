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
from hiraku.record import Record, open_workspace


def test_execution_resolves_moved_record_in_external_knowledge_base(tmp_path):
    config = HirakuConfig.init(tmp_path / "code", records_path="../knowledge")
    workspace = open_workspace(config)
    record = Record.create(workspace, "original.md", id="stable")
    config.set_active_record(record)
    moved = config.records_dir / "renamed.md"
    record.path.rename(moved)
    result = execute_command(
        [
            sys.executable,
            "-c",
            "import os; print(os.getcwd()); print(os.environ['HIRAKU_RECORD_ID']); "
            "print(os.environ['HIRAKU_RECORD_FILE'])",
        ],
        record_id="stable",
        config=config,
        capture_output=True,
    )
    assert result.returncode == 0
    assert result.stdout.splitlines() == [str(config.root), "stable", str(moved)]


def test_run_executes_in_project_and_returns_output_and_exit_code(
    tmp_path: Path,
) -> None:
    config = HirakuConfig.init(tmp_path, records_path="hiraku/records")
    workspace = open_workspace(config)
    _created_record = Record.create(workspace, "root/root.md", id="root")
    config.set_active_record(_created_record)
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
    config = HirakuConfig.init(tmp_path, records_path="hiraku/records")
    workspace = open_workspace(config)
    _created_record = Record.create(workspace, "root/root.md", id="root")
    config.set_active_record(_created_record)
    (tmp_path / "uncommitted.txt").write_text("dirty")
    command = [
        sys.executable,
        "-c",
        "import os; print('|'.join(os.environ[k] for k in "
        "('HIRAKU_RECORD_ID','HIRAKU_COMMIT','HIRAKU_GIT_STATE'))) ",
    ]
    result = execute_command(
        command, record_id="root", config=config, capture_output=True
    )
    record_id, commit, git_state = result.stdout.strip().split("|")
    assert record_id == "root"
    assert commit == ""
    assert git_state == "unavailable"


def test_run_requires_explicit_command_and_streams_output(
    tmp_path: Path, monkeypatch
) -> None:
    config = HirakuConfig.init(tmp_path, records_path="hiraku/records")
    monkeypatch.chdir(tmp_path)
    workspace = open_workspace(config)
    _created_record = Record.create(workspace, "root/root.md", id="root")
    config.set_active_record(_created_record)

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
    config = HirakuConfig.init(tmp_path, records_path="hiraku/records")
    workspace = open_workspace(config)
    _created_record = Record.create(workspace, "root/root.md", id="root")
    config.set_active_record(_created_record)
    with pytest.raises(ValidationError, match="reserved run attribution"):
        execute_command(
            ["echo", "hello"], env={"HIRAKU_RECORD_ID": "other"}, config=config
        )


def test_cli_propagates_nonzero_exit_code(tmp_path: Path, monkeypatch) -> None:
    config = HirakuConfig.init(tmp_path, records_path="hiraku/records")
    workspace = open_workspace(config)
    _created_record = Record.create(workspace, "root/root.md", id="root")
    config.set_active_record(_created_record)
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
