"""Git inspection and optional repository initialization."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .errors import GitError
from .project import Project


class GitRepository:
    """Inspect the Git repository containing a Hiraku project.

    Example:
        >>> from tempfile import TemporaryDirectory
        >>> with TemporaryDirectory() as directory:
        ...     path = Path(directory)
        ...     _ = subprocess.run(["git", "init", "-q", str(path)], check=True)
        ...     _ = subprocess.run(
        ...         ["git", "-C", str(path), "config", "user.name", "Test"], check=True
        ...     )
        ...     _ = subprocess.run(
        ...         [
        ...             "git", "-C", str(path), "config", "user.email",
        ...             "test@example.invalid",
        ...         ],
        ...         check=True,
        ...     )
        ...     _ = (path / "README.md").write_text("Research\\n")
        ...     _ = subprocess.run(
        ...         ["git", "-C", str(path), "add", "README.md"], check=True
        ...     )
        ...     _ = subprocess.run(
        ...         [
        ...             "git", "-C", str(path), "-c", "commit.gpgsign=false",
        ...             "commit", "-qm", "Initial",
        ...         ],
        ...         check=True,
        ...     )
        ...     repo = GitRepository(path)
        ...     [
        ...         len(repo.resolve("HEAD")),
        ...         repo.current_branch is not None,
        ...         repo.is_dirty,
        ...     ]
        [40, True, False]
    """

    def __init__(self, root: Path) -> None:
        """Open the repository containing *root*, including parent repositories.

        Args:
            root: A project directory inside a Git working tree.

        Raises:
            GitError: If Git is unavailable or *root* is outside a repository.
        """
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
        """
        Run a Git command in the repository root.

        Args:
            arguments: The Git command and its arguments.
            check: If True, raise GitError on non-zero exit code.

        Returns:
            The completed process.

        Raises:
            GitError: If the command fails or cannot be executed.

        Examples:
            >>> repo = GitRepository(Path("."))
            >>> result = repo._run("rev-parse", "HEAD")
            >>> result.returncode
            0
            >>> len(result.stdout.strip()) == 40
            True
        """
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
        """
        Resolve a Git reference to a commit SHA.

        Args:
            ref: The Git reference to resolve.

        Returns:
            The commit SHA corresponding to the reference.

        Raises:
            GitError: If the reference cannot be resolved.

        Examples:
            >>> repo = GitRepository(Path("."))
            >>> sha = repo.resolve("HEAD")
            >>> len(sha)
            40
            >>> repo.resolve("nonexistent")  # doctest: +ELLIPSIS
            Traceback (most recent call last):
            ...
            hiraku.errors.GitError: ...
        """
        if not isinstance(ref, str) or not ref:
            raise GitError(f"Invalid Git ref: {ref!r}")
        return self._run("rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()

    @property
    def current_branch(self) -> str | None:
        """Return the current branch name, or ``None`` for detached HEAD.

        Examples:
            >>> repo = GitRepository(Path("."))
            >>> isinstance(repo.current_branch, (str, type(None)))
            True
        """
        result = self._run("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    @property
    def is_dirty(self) -> bool:
        """Return whether the repository has staged or unstaged changes.

        Examples:
            >>> repo = GitRepository(Path("."))
            >>> isinstance(repo.is_dirty, bool)
            True
        """
        return bool(
            self._run("status", "--porcelain", "--untracked-files=normal").stdout
        )


def require_git() -> str:
    """Return the Git executable path or raise if Git is unavailable.

    Examples:
        >>> Path(require_git()).name in {"git", "git.exe"}
        True

    Raises:
        GitError: If Git is not available on ``PATH``.
    """
    executable = shutil.which("git")
    if executable is None:
        raise GitError("Git executable was not found on PATH")
    return executable


def init_code_repository(project: Project) -> None:
    """Initialize a repository for *project*, preserving existing history.

    Args:
        project: The project whose root should become a Git working tree.

    Examples:
        >>> from tempfile import TemporaryDirectory
        >>> with TemporaryDirectory() as directory:
        ...     project = Project.create(
        ...         directory, install_skill=False, install_mcp=False
        ...     )
        ...     init_code_repository(project)
        ...     GitRepository(project.root).current_branch is not None
        True

    Raises:
        GitError: If Git is unavailable or initialization fails.
    """
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
