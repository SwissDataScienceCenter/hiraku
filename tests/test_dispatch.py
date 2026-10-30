from __future__ import annotations

import os
import subprocess
from pathlib import Path

from hiraku.config import HirakuConfig


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
    config = HirakuConfig.init(path)
    (path / "marker.txt").write_text("committed", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "initial")
    return config


def _run(
    script: Path,
    *arguments: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(script), *arguments],
        cwd=script.parents[2],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_launch_dispatcher_reports_current_commit_and_git_state(
    tmp_path: Path,
) -> None:
    config = _repository(tmp_path)
    head = _git(tmp_path, "rev-parse", "HEAD")
    script = config.dispatch_dir / "launch.sh"
    environment = os.environ.copy()
    environment.update(
        HIRAKU_RECORD_ID="task",
        HIRAKU_RECORD_FILE="/records/task.md",
        HIRAKU_ENTRYPOINT="train",
    )
    command = [
        "--",
        "/bin/sh",
        "-c",
        'printf "%s|%s|%s|%s|%s" "$HIRAKU_RECORD_ID" "$HIRAKU_RECORD_FILE" '
        '"$HIRAKU_ENTRYPOINT" "$HIRAKU_COMMIT" "$HIRAKU_GIT_STATE"',
    ]

    local = _run(script, *command, env=environment)
    assert local.returncode == 0
    assert local.stdout == f"task|/records/task.md|train|{head}|clean"
    assert local.stderr == ""

    _git(tmp_path, "commit", "--allow-empty", "-qm", "second")
    current = _git(tmp_path, "rev-parse", "HEAD")
    expected_is_ignored = _run(
        script, "--commit", "not-a-ref", *command, env=environment
    )
    assert expected_is_ignored.returncode == 0
    assert expected_is_ignored.stdout == (
        f"task|/records/task.md|train|{current}|clean"
    )
    assert expected_is_ignored.stderr == ""

    (tmp_path / "dirty.txt").write_text("dirty", encoding="utf-8")
    dirty = _run(script, *command, env=environment)
    assert dirty.returncode == 0
    assert dirty.stdout == f"task|/records/task.md|train|{current}|dirty"
    assert dirty.stderr == ""


def test_launch_dispatcher_preserves_context_and_reports_unavailable_git(
    tmp_path: Path,
) -> None:
    config = HirakuConfig.init(tmp_path)
    inherited = os.environ.copy()
    inherited.update(
        HIRAKU_RECORD_ID="stale-record",
        HIRAKU_COMMIT="stale-commit",
        HIRAKU_GIT_STATE="clean",
    )
    result = _run(
        config.dispatch_dir / "launch.sh",
        "--",
        "/bin/sh",
        "-c",
        'printf "%s|%s|%s" "$HIRAKU_RECORD_ID" "$HIRAKU_COMMIT" "$HIRAKU_GIT_STATE"',
        env=inherited,
    )
    assert result.returncode == 0
    assert result.stdout == "stale-record||unavailable"


def test_run_uses_dispatcher_in_current_working_tree(tmp_path: Path) -> None:
    from hiraku.execution import execute_command
    from hiraku.record import Record

    config = _repository(tmp_path)
    Record.create("test", config=config)
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
