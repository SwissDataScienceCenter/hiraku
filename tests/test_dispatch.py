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
        HIRAKU_TASK_ID="task",
        HIRAKU_TASK_FILE="/records/task.md",
        HIRAKU_ENTRYPOINT="train",
    )
    command = [
        "--",
        "/bin/sh",
        "-c",
        'printf "%s|%s|%s|%s|%s" "$HIRAKU_TASK_ID" "$HIRAKU_TASK_FILE" '
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
        HIRAKU_TASK_ID="stale-task",
        HIRAKU_EXPERIMENT_ID="stale-experiment",
        HIRAKU_COMMIT="stale-commit",
        HIRAKU_GIT_STATE="clean",
    )
    result = _run(
        config.dispatch_dir / "launch.sh",
        "--",
        "/bin/sh",
        "-c",
        'printf "%s|%s|%s|%s" "$HIRAKU_TASK_ID" '
        '"$HIRAKU_EXPERIMENT_ID" "$HIRAKU_COMMIT" "$HIRAKU_GIT_STATE"',
        env=inherited,
    )
    assert result.returncode == 0
    assert result.stdout == "stale-task|stale-experiment||unavailable"


def test_worktree_dispatcher_isolates_and_cleans_up(tmp_path: Path) -> None:
    config = _repository(tmp_path)
    commit = _git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "marker.txt").write_text("working", encoding="utf-8")
    before = _git(tmp_path, "worktree", "list", "--porcelain")

    result = _run(
        config.dispatch_dir / "worktree.sh",
        "--commit",
        commit,
        "--",
        "/bin/sh",
        "-c",
        'printf "%s|%s|%s|%s" "$(cat marker.txt)" "$HIRAKU_COMMIT" '
        '"$HIRAKU_GIT_STATE" "$HIRAKU_ROOT"',
        env={**os.environ, "HIRAKU_ROOT": str(tmp_path)},
    )

    assert result.returncode == 0
    assert result.stdout == f"committed|{commit}|clean|{tmp_path}"
    assert "worktree.sh: created temporary worktree at " in result.stderr
    assert f" ({commit})" in result.stderr
    assert "worktree.sh: running from " in result.stderr
    assert _git(tmp_path, "worktree", "list", "--porcelain") == before

    failed = _run(
        config.dispatch_dir / "worktree.sh",
        "--commit",
        commit,
        "--",
        "/bin/sh",
        "-c",
        "exit 7",
    )
    assert failed.returncode == 7
    assert _git(tmp_path, "worktree", "list", "--porcelain") == before
