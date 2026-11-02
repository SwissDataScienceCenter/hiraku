from __future__ import annotations

import subprocess
from pathlib import Path

from hiraku.config import HirakuConfig
from hiraku.record import Record, open_workspace


def _git(path: Path, *arguments: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=path,
        text=True,
        capture_output=True,
        check=check,
    )
    return result.stdout.strip()


def _repository(path: Path) -> HirakuConfig:
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "config", "user.name", "Test")
    config = HirakuConfig.init(path, records_path="hiraku/records")
    (path / "marker.txt").write_text("committed", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "initial")
    return config


def test_run_uses_dispatcher_in_current_working_tree(tmp_path: Path) -> None:
    from hiraku.execution import execute_command

    config = _repository(tmp_path)
    workspace = open_workspace(config)
    _created_record = Record.create(workspace, "test/test.md", id="test")
    config.set_active_record(_created_record)
    config.dispatch_dir.mkdir()
    (config.dispatch_dir / "test.sh").write_text(
        '#!/bin/sh\n[ "$1" = "--" ] && shift\nexec "$@"\n', encoding="utf-8"
    )
    (config.dispatch_dir / "test.sh").chmod(0o755)
    result = execute_command(
        ["/bin/sh", "-c", "cat marker.txt; printf output > result.txt"],
        record_id="test",
        dispatch="test",
        config=config,
        capture_output=True,
    )
    assert result.returncode == 0
    assert result.stdout == "committed"
    assert (tmp_path / "result.txt").read_text() == "output"
