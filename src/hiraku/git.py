"""Git inspection and optional repository initialization."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .errors import GitError
from .project import Project


class GitRepository:
    """The Git repository containing a Hiraku project."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.git = require_git()
        result = self._run("-C", str(self.root), "rev-parse", "--show-toplevel")
        repository_root = Path(result.stdout.strip()).resolve()
        try:
            self.root.relative_to(repository_root)
        except ValueError as exc:
            raise GitError(
                f"Project {self.root} is outside Git repository {repository_root}"
            ) from exc
        self.root = repository_root

    def _run(
        self, *arguments: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                [self.git, *arguments],
                cwd=self.root,
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            raise GitError(
                f"Could not execute Git command {arguments!r}: {exc}"
            ) from exc
        if check and result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise GitError(f"Git command failed ({' '.join(arguments)}): {detail}")
        return result

    def resolve(self, ref: str) -> str:
        if not isinstance(ref, str) or not ref:
            raise GitError(f"Invalid Git ref: {ref!r}")
        return self._run("rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()

    @property
    def origin_url(self) -> str | None:
        """Return the configured origin URL without contacting the remote."""
        result = self._run("remote", "get-url", "origin", check=False)
        return (result.stdout.strip() or None) if result.returncode == 0 else None

    @property
    def current_branch(self) -> str | None:
        result = self._run("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    @property
    def is_dirty(self) -> bool:
        return bool(
            self._run("status", "--porcelain", "--untracked-files=normal").stdout
        )


def require_git() -> str:
    """Fail before provisioning when an explicitly requested Git tool is absent."""
    executable = shutil.which("git")
    if executable is None:
        raise GitError("Git executable was not found on PATH")
    return executable


def init_code_repository(project: Project) -> None:
    """Initialize the code repository, preserving existing repositories/worktrees."""
    require_git()
    if (project.root / ".git").exists():
        GitRepository(project.root)
        return
    git = require_git()
    try:
        result = subprocess.run(
            [git, "init", "--", str(project.root)],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise GitError(f"Could not initialize Git repository: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise GitError(f"Git init failed: {detail}")
