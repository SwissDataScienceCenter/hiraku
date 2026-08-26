"""Project context: the files the agent reads before it designs anything.

An agent that has never seen the project asks the project's own questions back at you — what is
`reprojection_error`, where is the data, which frames are held out. Those answers usually already
exist somewhere in the repository: `CLAUDE.md`, the README, `docs/`, a notes file. This module
collects them into a bounded digest that rides along with the state in the prompt.

Three properties matter and are enforced here rather than left to the agent:

- **Bounded.** Per-file and total byte caps, a file-count cap, text suffixes only. A prompt that
  silently grows to a megabyte fails in a way nobody can debug.
- **Auditable.** `hiraku-auto context` prints exactly which files would be sent and how large the
  excerpt is, so you can look before anything leaves the machine. Nothing here is implicit.
- **Background, not evidence.** The prompt rules say so explicitly: a claim from a context file may
  motivate an experiment and may supply a definition, but it is never a result and never a number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Project
from .errors import HirakuError

# Conventional places a project already keeps its own description. Deliberately narrow: memory
# files, the README, prose documentation. Source trees are not swept — point at a file explicitly
# with `--context path` when a specific module is the thing the agent needs to read.
DEFAULT_SOURCES: tuple[str, ...] = (
    "CLAUDE.md",
    "AGENTS.md",
    ".claude/CLAUDE.md",
    "README.md",
    "docs/**/*.md",
    "notes/**/*.md",
    "research/config.md",
)

DEFAULT_LIMITS: dict[str, int] = {
    "max_files": 40,
    "max_bytes_per_file": 20_000,
    "max_total_bytes": 200_000,
}

TEXT_SUFFIXES = {
    ".md", ".markdown", ".rst", ".txt", ".yml", ".yaml", ".toml", ".json",
    ".cfg", ".ini", ".py", ".sh", ".csv",
}

# Never walked, whatever a glob says: build output, environments, and the records the state already
# carries. Sending `research/goals/*.md` here would duplicate the goal the agent is being asked
# about, at full length, in a second place in the prompt.
SKIP_PARTS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".idea", ".vscode", "site-packages",
    ".hiraku", "experiments", "_to_delete", "dist", "build", ".eggs",
}
SKIP_RELATIVE_PREFIXES = ("research/goals/", "research/experiments/", "research/proposals/")


@dataclass
class ContextFile:
    rel: str
    text: str
    bytes_total: int
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"path": self.rel, "content": self.text}
        if self.truncated:
            out["truncated"] = f"first {len(self.text)} of {self.bytes_total} characters"
        return out


@dataclass
class Bundle:
    """What would be, or was, sent. Empty is a legitimate answer, not an error."""

    files: list[ContextFile] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    limits: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_LIMITS))

    @property
    def total_chars(self) -> int:
        return sum(len(f.text) for f in self.files)

    def __bool__(self) -> bool:
        return bool(self.files)

    def to_state(self) -> dict[str, Any]:
        return {
            "note": (
                "Background on the project, read from its own files. Use it for definitions, "
                "paths, conventions and prior decisions. It is NOT evidence: never quote a number "
                "from here as a result."
            ),
            "sources": self.sources,
            "files": [f.to_dict() for f in self.files],
            "omitted": self.skipped,
        }

    def summary(self) -> str:
        if not self.files:
            return "context: none"
        return (
            f"context: {len(self.files)} file(s), {self.total_chars:,} characters"
            + (f", {len(self.skipped)} omitted" if self.skipped else "")
        )


# --------------------------------------------------------------------------- configuration


def configured(project: Project) -> tuple[list[str], dict[str, int]]:
    """`context:` from `research/config.md`, falling back to the defaults.

    A project that has never touched the key gets the conventional sources. Setting
    `context: {sources: []}` explicitly turns collection off, which is different from not
    having configured it at all.
    """
    block: Any = {}
    if project.config_md.is_file():
        from .records import read_record

        block = read_record(project.config_md).front.get("context") or {}
    if not isinstance(block, dict):
        block = {}

    raw = block.get("sources")
    if raw is None:
        sources = list(DEFAULT_SOURCES)
    elif isinstance(raw, str):
        sources = [raw]
    elif isinstance(raw, list):
        sources = [str(s) for s in raw if str(s).strip()]
    else:
        sources = list(DEFAULT_SOURCES)

    limits = dict(DEFAULT_LIMITS)
    for key in limits:
        value = block.get(key)
        if isinstance(value, int) and value > 0:
            limits[key] = value
    return sources, limits


# --------------------------------------------------------------------------- collection


def _excluded(path: Path, rel: str) -> bool:
    if any(part in SKIP_PARTS for part in path.parts):
        return True
    if any(rel.startswith(prefix) for prefix in SKIP_RELATIVE_PREFIXES):
        return True
    return path.name.startswith(".") and path.suffix not in TEXT_SUFFIXES


def _resolve(project: Project, pattern: str) -> list[Path]:
    """One source pattern to the files it names, inside the project only."""
    candidate = Path(pattern)
    if candidate.is_absolute():
        try:
            candidate.relative_to(project.root)
        except ValueError as exc:
            raise HirakuError(
                f"Context source '{pattern}' is outside the project. Only files under "
                f"{project.root} can be sent."
            ) from exc
        return [candidate] if candidate.is_file() else []

    if any(ch in pattern for ch in "*?["):
        return sorted(p for p in project.root.glob(pattern) if p.is_file())
    direct = project.root / pattern
    if direct.is_dir():
        return sorted(p for p in direct.rglob("*.md") if p.is_file())
    return [direct] if direct.is_file() else []


def collect(
    project: Project,
    *,
    extra: list[str] | None = None,
    enabled: bool = True,
) -> Bundle:
    """Read the configured sources plus any `extra` paths, under the configured caps."""
    if not enabled:
        return Bundle()

    sources, limits = configured(project)
    sources = sources + [s for s in (extra or []) if s not in sources]
    bundle = Bundle(sources=sources, limits=limits)
    if not sources:
        return bundle

    seen: set[Path] = set()
    budget = limits["max_total_bytes"]

    for pattern in sources:
        for path in _resolve(project, pattern):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            rel = project.rel(path)

            if _excluded(path, rel):
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES:
                bundle.skipped.append(f"{rel} (not a text file)")
                continue
            if len(bundle.files) >= limits["max_files"]:
                bundle.skipped.append(f"{rel} (file cap {limits['max_files']} reached)")
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                bundle.skipped.append(f"{rel} (unreadable: {exc})")
                continue
            if not text.strip():
                continue

            total = len(text)
            allowance = min(limits["max_bytes_per_file"], budget)
            if allowance <= 0:
                bundle.skipped.append(f"{rel} (total cap {limits['max_total_bytes']} reached)")
                continue
            truncated = total > allowance
            excerpt = text[:allowance].rstrip() if truncated else text.rstrip()
            budget -= len(excerpt)
            bundle.files.append(
                ContextFile(rel=rel, text=excerpt, bytes_total=total, truncated=truncated)
            )

    return bundle
