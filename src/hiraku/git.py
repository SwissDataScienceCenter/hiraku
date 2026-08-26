from __future__ import annotations

import subprocess
from pathlib import Path

from hiraku.errors import HirakuError


def run_git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown Git error"
        raise HirakuError(detail)
    return result


def repository_root(start: Path) -> Path:
    result = run_git(start, "rev-parse", "--show-toplevel", check=False)
    if result.returncode:
        raise HirakuError("Hiraku must be used inside a Git repository.")
    return Path(result.stdout.strip()).resolve()


def current_branch(root: Path) -> str | None:
    result = run_git(root, "branch", "--show-current")
    value = result.stdout.strip()
    return value or None


def is_clean(root: Path) -> bool:
    return not run_git(root, "status", "--porcelain").stdout.strip()


def branch_exists(root: Path, branch: str) -> bool:
    return (
        run_git(
            root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False
        ).returncode
        == 0
    )


def ensure_branch(root: Path, branch: str) -> bool:
    if branch_exists(root, branch):
        return False
    run_git(root, "branch", branch, "HEAD")
    return True


def resolve_commit(root: Path, ref: str) -> str:
    result = run_git(root, "rev-parse", "--verify", f"{ref}^{{commit}}", check=False)
    if result.returncode:
        raise HirakuError(f"Git ref {ref!r} does not resolve to a commit.")
    return result.stdout.strip()


def commit_parents(root: Path, commit: str) -> list[str]:
    result = run_git(root, "show", "-s", "--format=%P", commit, check=False)
    if result.returncode:
        return []
    return result.stdout.strip().split()
