from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .errors import HirakuError


class GitAdapter:
    def __init__(self, root: Path):
        self.root = root

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", *args], cwd=self.root, text=True, capture_output=True, check=False
        )
        if check and result.returncode:
            detail = result.stderr.strip() or result.stdout.strip() or "Git command failed."
            raise HirakuError(detail)
        return result

    def ensure_repository(self) -> None:
        if self._run("rev-parse", "--is-inside-work-tree", check=False).stdout.strip() != "true":
            raise HirakuError("Hiraku requires a Git repository.")

    def ensure_clean(self) -> None:
        if self._run("status", "--porcelain", "--untracked-files=all").stdout.strip():
            raise HirakuError("The Git working tree must be completely clean.")

    def head_commit(self) -> str:
        result = self._run("rev-parse", "--verify", "HEAD", check=False)
        if result.returncode:
            raise HirakuError("The repository needs at least one commit.")
        return result.stdout.strip()

    def resolve_commit(self, ref: str) -> str:
        result = self._run("rev-parse", "--verify", f"{ref}^{{commit}}", check=False)
        if result.returncode:
            raise HirakuError(f"Unknown Git commit or ref: {ref}")
        return result.stdout.strip()

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        return (
            self._run("merge-base", "--is-ancestor", ancestor, descendant, check=False).returncode
            == 0
        )

    def current_branch(self) -> str:
        result = self._run("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
        if result.returncode:
            raise HirakuError("The current checkout is detached; switch to the Experiment branch.")
        return result.stdout.strip()

    def branch_exists(self, branch: str) -> bool:
        return self._run("show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False).returncode == 0

    def has_changes(self) -> bool:
        return bool(self._run("status", "--porcelain", "--untracked-files=all").stdout.strip())

    def commit_all(self, message: str) -> str:
        if not self.has_changes():
            raise HirakuError("The implementation command produced no source changes.")
        self._run("add", "-A")
        self._run("commit", "--no-gpg-sign", "-m", message)
        return self.head_commit()

    def create_and_checkout_branch(self, branch: str, start_commit: str) -> str:
        self.ensure_clean()
        if self.branch_exists(branch):
            raise HirakuError(f"Git branch already exists: {branch}")
        original = self.current_branch()
        self._run("switch", "-c", branch, start_commit)
        return original

    def rollback_created_branch(self, original_branch: str, created_branch: str) -> None:
        self._run("switch", original_branch, check=False)
        self._run("branch", "-D", created_branch, check=False)

    @contextmanager
    def detached_worktree(self, commit: str) -> Iterator[Path]:
        with tempfile.TemporaryDirectory(prefix="hiraku-run-") as directory:
            path = Path(directory)
            result = self._run("worktree", "add", "--detach", str(path), commit, check=False)
            if result.returncode:
                raise HirakuError(result.stderr.strip() or "Could not create temporary worktree.")
            try:
                yield path
            finally:
                self._run("worktree", "remove", "--force", str(path), check=False)
