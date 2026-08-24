"""Project discovery and configuration.

A project is the directory containing `.hiraku.yml`. Every command finds it by walking up from the
working directory, so commands work from inside a worktree or a subdirectory.
"""

from __future__ import annotations

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
    def research_dir(self) -> Path:
        return self._contained(self.raw.get("research_dir") or "research")

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
        return self._contained(tracking.get("path") or ".hiraku/runs.csv")

    def _contained(self, relative: str) -> Path:
        """Resolve a configured path inside the project, rejecting absolute paths and traversal."""
        candidate = Path(relative)
        if candidate.is_absolute():
            raise HirakuError(f"Configured path must be relative to the project: {relative}")
        resolved = (self.root / candidate).resolve()
        if not str(resolved).startswith(str(self.root.resolve())):
            raise HirakuError(f"Configured path escapes the project: {relative}")
        return resolved

    def rel(self, path: Path) -> str:
        try:
            return str(Path(path).resolve().relative_to(self.root.resolve()))
        except ValueError:
            return str(path)


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
