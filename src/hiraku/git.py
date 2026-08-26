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


def validate_branch_name(root: Path, branch: str) -> str:
    if not branch or run_git(root, "check-ref-format", "--branch", branch, check=False).returncode:
        raise HirakuError(f"Invalid Git branch name {branch!r}.")
    return branch


def switch_branch(root: Path, branch: str) -> None:
    run_git(root, "switch", branch)


def create_branch(root: Path, branch: str, start: str) -> bool:
    validate_branch_name(root, branch)
    if branch_exists(root, branch):
        return False
    run_git(root, "branch", branch, start)
    return True


def is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    result = run_git(root, "merge-base", "--is-ancestor", ancestor, descendant, check=False)
    return result.returncode == 0


def merge_base(root: Path, left: str, right: str) -> str | None:
    result = run_git(root, "merge-base", left, right, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def commits_between(root: Path, base: str, head: str) -> list[str]:
    result = run_git(root, "rev-list", "--reverse", f"{base}..{head}")
    return list(filter(None, result.stdout.splitlines()))


def changed_paths(root: Path, base: str, head: str) -> list[str]:
    result = run_git(root, "diff", "--name-only", f"{base}...{head}")
    return sorted(filter(None, result.stdout.splitlines()))


def ahead_behind(root: Path, left: str, right: str) -> tuple[int, int] | None:
    result = run_git(root, "rev-list", "--left-right", "--count", f"{left}...{right}", check=False)
    if result.returncode:
        return None
    left_count, right_count = result.stdout.split()
    return int(left_count), int(right_count)


def ancestry_distance(root: Path, ancestor: str, descendant: str) -> int | None:
    if not is_ancestor(root, ancestor, descendant):
        return None
    result = run_git(root, "rev-list", "--ancestry-path", "--count", f"{ancestor}..{descendant}")
    return int(result.stdout.strip())


def remote_names(root: Path) -> list[str]:
    return sorted(filter(None, run_git(root, "remote").stdout.splitlines()))


def remote_url(root: Path, remote: str, push: bool = False) -> str:
    args = ("remote", "get-url", "--push", remote) if push else ("remote", "get-url", remote)
    result = run_git(root, *args, check=False)
    if result.returncode:
        raise HirakuError(f"Git remote {remote!r} does not exist or has no usable URL.")
    return result.stdout.strip()


def branch_upstream_remote(root: Path, branch: str) -> str | None:
    result = run_git(root, "config", "--get", f"branch.{branch}.remote", check=False)
    value = result.stdout.strip()
    return value if result.returncode == 0 and value and value != "." else None


def remote_default_branch(root: Path, remote: str) -> str:
    symbolic = run_git(root, "symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD", check=False)
    if symbolic.returncode == 0 and symbolic.stdout.strip().startswith(f"{remote}/"):
        return symbolic.stdout.strip()[len(remote) + 1 :]
    result = run_git(root, "ls-remote", "--symref", remote, "HEAD", check=False)
    for line in result.stdout.splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
            return line.removeprefix("ref: refs/heads/").removesuffix("\tHEAD")
    raise HirakuError(f"Could not determine the default branch for remote {remote!r}; pass --base.")


def branch_published(root: Path, remote: str, branch: str) -> bool:
    result = run_git(root, "ls-remote", "--exit-code", "--heads", remote, branch, check=False)
    return result.returncode == 0 and bool(result.stdout.strip())


def remote_branch_commit(root: Path, remote: str, branch: str) -> str:
    result = run_git(root, "ls-remote", "--heads", remote, branch, check=False)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if result.returncode or len(lines) != 1:
        raise HirakuError(f"Remote branch {remote}/{branch} does not resolve uniquely.")
    return lines[0].split()[0]


def push_branch(root: Path, remote: str, branch: str) -> None:
    run_git(root, "push", remote, f"{branch}:refs/heads/{branch}")


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
