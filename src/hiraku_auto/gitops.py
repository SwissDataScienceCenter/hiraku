"""Git operations: branches, worktrees, commits.

Thin wrappers over the `git` binary. Nothing here rewrites history, and nothing deletes work —
worktree removal takes `--force` only from an explicit command.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .errors import HirakuError


def run_git(root: Path, *args: str, check: bool = True) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:  # pragma: no cover
        raise HirakuError("git is not installed or not on PATH.") from exc
    if check and result.returncode != 0:
        raise HirakuError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def is_repo(root: Path) -> bool:
    try:
        return run_git(root, "rev-parse", "--is-inside-work-tree") == "true"
    except HirakuError:
        return False


def has_commits(root: Path) -> bool:
    """False for a fresh repository with an unborn HEAD, where no branch can be created yet."""
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"], cwd=str(root), capture_output=True
    )
    return result.returncode == 0


def head_commit(root: Path) -> str:
    if not has_commits(root):
        return ""
    return run_git(root, "rev-parse", "HEAD")


def resolve_ref(root: Path, ref: str) -> str:
    sha = run_git(root, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if not sha:
        raise HirakuError(f"Could not resolve ref '{ref}'.")
    return sha


def is_dirty(root: Path) -> bool:
    return bool(run_git(root, "status", "--porcelain"))


def current_branch(root: Path) -> str:
    return run_git(root, "rev-parse", "--abbrev-ref", "HEAD")


def branch_exists(root: Path, name: str) -> bool:
    result = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{name}"],
        cwd=str(root),
        capture_output=True,
    )
    return result.returncode == 0


def validate_branch_name(root: Path, name: str) -> str:
    """Reject names git will not accept — including the parent/child collision.

    `refs/heads/exp/foo` and `refs/heads/exp/foo/bar` cannot coexist, because a loose ref is a file
    and cannot also be a directory. That is why branch names stay flat and hierarchy lives in the
    record's `parent` field.
    """
    result = subprocess.run(
        ["git", "check-ref-format", "--branch", name], cwd=str(root), capture_output=True
    )
    if result.returncode != 0:
        raise HirakuError(f"'{name}' is not a valid git branch name.")
    prefix = name.rsplit("/", 1)[0] if "/" in name else ""
    if prefix and branch_exists(root, prefix):
        raise HirakuError(
            f"Cannot create branch '{name}': branch '{prefix}' already exists, and git cannot "
            f"hold both (a ref is a file, so it cannot also be a directory). Use a flat name."
        )
    return name


def create_branch(root: Path, name: str, start_point: str | None = None) -> str:
    validate_branch_name(root, name)
    if branch_exists(root, name):
        return name
    args = ["branch", name]
    if start_point:
        args.append(start_point)
    run_git(root, *args)
    return name


def worktree_list(root: Path) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in run_git(root, "worktree", "list", "--porcelain").splitlines():
        if not line.strip():
            if current:
                out.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    if current:
        out.append(current)
    return out


def worktree_add(root: Path, path: Path, branch: str) -> Path:
    if path.exists():
        return path
    if not branch_exists(root, branch):
        create_branch(root, branch)
    path.parent.mkdir(parents=True, exist_ok=True)
    run_git(root, "worktree", "add", str(path), branch)
    return path


def worktree_remove(root: Path, path: Path, force: bool = False) -> None:
    args = ["worktree", "remove", str(path)]
    if force:
        args.append("--force")
    run_git(root, *args)


def add_temp_worktree(root: Path, commit: str, destination: Path) -> Path:
    """A detached worktree at a commit, for a reproducible launch."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    run_git(root, "worktree", "add", "--detach", str(destination), commit)
    return destination
