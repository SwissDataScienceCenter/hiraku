from __future__ import annotations

import subprocess
from pathlib import Path

from hiraku.errors import HirakuError


def _git(
    args: list[str],
    *,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise HirakuError("Git is required but was not found on PATH.") from exc
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown Git error"
        raise HirakuError(f"Git command failed: {detail}")
    return result


def top_level(cwd: Path) -> Path:
    result = _git(["rev-parse", "--show-toplevel"], cwd=cwd)
    return Path(result.stdout.strip()).resolve()


def current_branch(cwd: Path) -> str:
    result = _git(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=cwd, check=False)
    if result.returncode != 0:
        raise HirakuError("A named Git branch is required; detached HEAD is not supported.")
    return result.stdout.strip()


def head_commit(cwd: Path) -> str:
    result = _git(["rev-parse", "--verify", "HEAD^{commit}"], cwd=cwd, check=False)
    if result.returncode != 0:
        raise HirakuError("The Git repository must contain at least one commit.")
    return result.stdout.strip()


def ensure_clean(cwd: Path) -> None:
    result = _git(["status", "--porcelain=v1", "--untracked-files=all"], cwd=cwd)
    if result.stdout:
        raise HirakuError("The Git working tree must be completely clean.")


def validate_branch_name(branch: str, cwd: Path) -> None:
    if not branch:
        raise HirakuError("The experiment branch cannot be empty.")
    result = _git(["check-ref-format", "--branch", branch], cwd=cwd, check=False)
    if result.returncode != 0:
        raise HirakuError(f"Invalid Git branch name: {branch}")


def local_branch_exists(branch: str, cwd: Path) -> bool:
    result = _git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=cwd, check=False)
    return result.returncode == 0


def switch_new_branch(branch: str, cwd: Path) -> None:
    _git(["switch", "-c", branch], cwd=cwd)


def switch_branch(branch: str, cwd: Path) -> None:
    _git(["switch", branch], cwd=cwd)


def delete_branch(branch: str, cwd: Path) -> None:
    _git(["branch", "-D", branch], cwd=cwd)
