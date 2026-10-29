"""Git inspection and optional repository initialization."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from .config import HirakuConfig
from .errors import GitError


class GitRepository:
    """The Git repository containing a Hiraku project."""

    def __init__(self, config: HirakuConfig | Path | str | None = None) -> None:
        require_git()
        if isinstance(config, HirakuConfig):
            start = config.root
            self.state_dir: Path | None = config.hiraku_dir
        else:
            start = Path.cwd() if config is None else Path(config)
            self.state_dir = None
        result = self._execute(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"], cwd=None
        )
        self.root = Path(result.stdout.strip()).resolve()
        project_root = Path(start).resolve()
        try:
            project_root.relative_to(self.root)
        except ValueError as exc:
            raise GitError(
                f"Project {project_root} is outside Git repository {self.root}"
            ) from exc

    @staticmethod
    def _execute(
        arguments: Sequence[str], *, cwd: Path | None, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                list(arguments), cwd=cwd, text=True, capture_output=True, check=False
            )
        except OSError as exc:
            raise GitError(
                f"Could not execute Git command {list(arguments)!r}: {exc}"
            ) from exc
        if check and result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise GitError(f"Git command failed ({' '.join(arguments)}): {detail}")
        return result

    def _git(
        self, *arguments: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return self._execute(["git", *arguments], cwd=self.root, check=check)

    def resolve(self, ref: str) -> str:
        if not isinstance(ref, str) or not ref:
            raise GitError(f"Invalid Git ref: {ref!r}")
        return self._git("rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()

    def job_store_pathspec(self) -> str | None:
        """Return a literal repository pathspec excluding Hiraku job state."""
        if self.state_dir is None:
            return None
        path = (self.state_dir / ".jobs").relative_to(self.root).as_posix()
        return f":(exclude,top,literal){path}"

    @property
    def current_branch(self) -> str | None:
        result = self._git("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
        return result.stdout.strip() if result.returncode == 0 else None


def require_git() -> None:
    """Fail before provisioning when an explicitly requested Git tool is absent."""
    if shutil.which("git") is None:
        raise GitError("Git executable was not found on PATH")


def init_code_repository(config: HirakuConfig) -> None:
    """Initialize the code repository, preserving existing repositories/worktrees."""
    require_git()
    if (config.root / ".git").exists():
        GitRepository(config)
        return
    GitRepository._execute(["git", "init", "--", str(config.root)], cwd=None)
