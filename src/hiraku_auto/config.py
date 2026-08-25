"""Project discovery and configuration.

A project is the directory containing `.hiraku.yml`. Every command finds it by walking up from the
working directory, so commands work from inside a worktree or a subdirectory.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import yamlio
from .errors import HirakuError

CONFIG_NAME = ".hiraku.yml"

DEFAULT_CONFIG: dict[str, Any] = {
    "experiments_dir": "experiments",
    "research_dir": "research",
    "tracking": {"backend": "table", "path": ".hiraku/runs.csv"},
}


@dataclass(frozen=True)
class Project:
    root: Path
    raw: dict[str, Any]

    @property
    def experiments_dir(self) -> Path:
        return self._contained(self.raw.get("experiments_dir") or "experiments")

    @property
    def shared_root(self) -> Path:
        """The main checkout, when this project was found inside a *linked* git worktree.

        A worktree is a full checkout, so `.hiraku.yml` is present in it and discovery stops
        there. The run table and the research contract must not fork per worktree, or parallel
        experiments would each keep a private table and nothing would be comparable.

        Only a linked worktree hoists. A project that merely sits inside some larger repository —
        `examples/natix-vo` in this one, say — is its own root and must stay that way.
        """
        return _main_checkout(self.root) or self.root

    @property
    def research_dir(self) -> Path:
        return self._contained(self.raw.get("research_dir") or "research", base=self.shared_root)

    @property
    def goals_dir(self) -> Path:
        return self.research_dir / "goals"

    @property
    def proposals_dir(self) -> Path:
        return self.research_dir / "proposals"

    @property
    def config_md(self) -> Path:
        return self.research_dir / "config.md"

    @property
    def leaderboard(self) -> Path:
        return self.research_dir / "leaderboard.md"

    @property
    def table_path(self) -> Path:
        tracking = self.raw.get("tracking") or {}
        return self._contained(tracking.get("path") or ".hiraku/runs.csv", base=self.shared_root)

    @property
    def in_worktree(self) -> bool:
        return self.shared_root != self.root

    def _contained(self, relative: str, base: Path | None = None) -> Path:
        """Resolve a configured path inside the project, rejecting absolute paths and traversal."""
        root = (base or self.root).resolve()
        candidate = Path(relative)
        if candidate.is_absolute():
            raise HirakuError(f"Configured path must be relative to the project: {relative}")
        resolved = (root / candidate).resolve()
        if not str(resolved).startswith(str(root)):
            raise HirakuError(f"Configured path escapes the project: {relative}")
        return resolved

    def rel(self, path: Path) -> str:
        try:
            return str(Path(path).resolve().relative_to(self.root.resolve()))
        except ValueError:
            return str(path)


def _git(root: Path, *args: str) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", *args], cwd=str(root), capture_output=True, text=True
    )
    out = result.stdout.strip()
    return out if result.returncode == 0 and out else None


def _main_checkout(root: Path) -> Path | None:
    """The same project inside the repository's main checkout, if `root` is in a linked worktree.

    In the main checkout `--git-dir` and `--git-common-dir` are the same path. In a linked worktree
    they differ: `<main>/.git/worktrees/<name>` versus `<main>/.git`. That difference is the only
    reliable signal, and it is what keeps a project nested inside an unrelated repository from
    being hoisted out of itself.
    """
    git_dir, common = _git(root, "--git-dir"), _git(root, "--git-common-dir")
    if git_dir is None or common is None:
        return None
    if (root / git_dir).resolve() == (root / common).resolve():
        return None  # the main checkout, or a plain repository
    common_path = (root / common).resolve()
    if common_path.name != ".git":
        return None
    toplevel = _git(root, "--show-toplevel")
    if toplevel is None:
        return None
    try:
        relative = root.resolve().relative_to(Path(toplevel).resolve())
    except ValueError:
        return None
    candidate = common_path.parent / relative
    return candidate if (candidate / CONFIG_NAME).is_file() else None


def find_project(start: Path | None = None) -> Project:
    current = (start or Path.cwd()).resolve()
    for directory in [current, *current.parents]:
        candidate = directory / CONFIG_NAME
        if candidate.is_file():
            return Project(root=directory, raw=yamlio.load_file(candidate) or {})
    raise HirakuError(
        f"No {CONFIG_NAME} found in {current} or any parent. Run `hiraku-auto init` first."
    )


def load_project(path: Path | None = None) -> Project:
    """Load the project rooted at `path` if given, else discover it from the working directory."""
    if path is None:
        return find_project()
    root = Path(path).resolve()
    if not (root / CONFIG_NAME).is_file():
        return find_project(root)
    return Project(root=root, raw=yamlio.load_file(root / CONFIG_NAME) or {})
