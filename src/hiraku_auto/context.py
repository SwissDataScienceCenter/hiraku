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

import glob
import os
import re
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
    "max_bytes_per_file": 60_000,
    "max_total_bytes": 400_000,
}

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.M)

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

# Refused wherever they appear, however the pattern was written. Context goes into the prompt
# verbatim, so a glob that wanders into a credential store is not a mistake worth allowing once.
SENSITIVE_PARTS = {
    ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".password-store", ".gem",
    "Keychains", "gcloud", "secrets", ".secrets",
}
SENSITIVE_NAMES = {
    ".netrc", "_netrc", ".npmrc", ".pypirc", ".git-credentials", "credentials", "id_rsa",
    "id_ed25519", ".env", ".env.local",
}


@dataclass
class ContextFile:
    rel: str
    text: str
    bytes_total: int
    truncated: bool = False
    dropped: list[str] = field(default_factory=list)

    @property
    def outside(self) -> bool:
        return self.rel.startswith("..") or self.rel.startswith("/")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"path": self.rel, "content": self.text}
        if self.outside:
            out["from"] = "another repository, named as a context source"
        if self.truncated:
            out["truncated"] = f"kept {len(self.text)} of {self.bytes_total} characters"
            if self.dropped:
                # Naming the dropped headings is the whole point. "first 20,000 of 167,131" tells
                # the agent nothing; a list of section titles lets it say "the resize convention is
                # probably under §Preprocessing, which I was not given" instead of guessing.
                out["sections_not_included"] = self.dropped
                out["how_to_handle_the_gap"] = (
                    "If an answer you need is likely under one of these headings, record it as an "
                    "**[UNSET: ...]** naming the heading — do not guess. The researcher can raise "
                    "context.max_bytes_per_file or paste that section in."
                )
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

    @property
    def truncated_files(self) -> list[ContextFile]:
        return [f for f in self.files if f.truncated]

    def needed_limits(self) -> tuple[int, int]:
        """The caps that would include every resolved file whole."""
        per_file = max([f.bytes_total for f in self.files] or [0])
        total = sum(f.bytes_total for f in self.files)
        return per_file, total

    def summary(self) -> str:
        if not self.files:
            return "context: none"
        outside = sum(1 for f in self.files if f.outside)
        cut = len(self.truncated_files)
        return (
            f"context: {len(self.files)} file(s), {self.total_chars:,} characters"
            + (f", {outside} from outside this project" if outside else "")
            + (f", {cut} truncated" if cut else "")
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


def _blocks(text: str) -> list[tuple[str | None, str]]:
    """`[(heading or None, block), ...]` — the preamble first, then one entry per heading."""
    marks = list(HEADING.finditer(text))
    if not marks:
        return [(None, text)]
    out: list[tuple[str | None, str]] = []
    if marks[0].start() > 0:
        out.append((None, text[: marks[0].start()]))
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        out.append((mark.group(2).strip(), text[mark.start() : end]))
    return out


def excerpt(text: str, allowance: int) -> tuple[str, list[str]]:
    """Cut to `allowance` on a heading boundary. Returns (kept text, dropped heading titles).

    Cutting mid-sentence at a byte count loses the one thing the reader needs to know: WHAT is
    missing. Cutting between sections keeps every included section whole and lets the excerpt say
    which sections it does not have, by name.
    """
    if len(text) <= allowance:
        return text, []
    blocks = _blocks(text)
    if len(blocks) == 1:
        return text[:allowance], []

    kept: list[str] = []
    dropped: list[str] = []
    used = 0
    for title, block in blocks:
        if used + len(block) <= allowance:
            kept.append(block)
            used += len(block)
        elif title:
            dropped.append(title)
        elif not kept:
            # A preamble too large to fit at all: take what there is room for.
            kept.append(block[:allowance])
            used = allowance
    if not kept:
        return text[:allowance], [t for t, _ in blocks if t]
    return "".join(kept), dropped


# --------------------------------------------------------------------------- collection


def _excluded(path: Path, rel: str) -> bool:
    if any(part in SKIP_PARTS for part in path.parts):
        return True
    if any(rel.startswith(prefix) for prefix in SKIP_RELATIVE_PREFIXES):
        return True
    return path.name.startswith(".") and path.suffix not in TEXT_SUFFIXES


def label(project: Project, path: Path) -> str:
    """How a file is named in the bundle, and to the agent.

    Inside the project, the project-relative path. Outside it, a relative path like
    `../mapillary_alignment/CLAUDE.md` — short, unambiguous about which repository it came from,
    and it does not put the researcher's home directory into a prompt.
    """
    resolved, root = Path(path).resolve(), project.root.resolve()
    try:
        return str(resolved.relative_to(root))
    except ValueError:
        return os.path.relpath(resolved, root)


def _refuse_sensitive(pattern: str, path: Path) -> None:
    """Credential stores are never context, however the pattern was written.

    Reaching outside the project is legitimate — the research project and the code it studies are
    usually separate repositories. Reaching into a keyring is not, and a `**` glob in the wrong
    place is how that happens by accident rather than on purpose.
    """
    parts = set(path.parts)
    if parts & SENSITIVE_PARTS or path.name in SENSITIVE_NAMES:
        raise HirakuError(
            f"Context source '{pattern}' resolves to {path}, which looks like credentials or "
            f"private keys. Context is sent to the agent verbatim, so this is refused."
        )


def _expand_directory(directory: Path) -> list[Path]:
    """A directory named as a source means its markdown, not its entire contents."""
    home = Path.home()
    if directory in {Path(directory.anchor), home} or directory.parent == Path(directory.anchor):
        raise HirakuError(
            f"Context source '{directory}' is a whole home or filesystem directory. Name the "
            f"specific folder or file you mean — a sweep from here would send far more than you "
            f"intend."
        )
    return sorted(p for p in directory.rglob("*.md") if p.is_file())


def _resolve(project: Project, pattern: str) -> list[Path]:
    """One source pattern to the files it names.

    Relative patterns resolve against the project root; absolute ones, and relative ones that
    climb out with `..`, are allowed and reach other repositories on purpose. Only credential
    locations and bare home/filesystem roots are refused.
    """
    raw = os.path.expanduser(pattern.strip())
    if not raw:
        return []
    has_glob = any(ch in raw for ch in "*?[")
    candidate = Path(raw)

    if candidate.is_absolute():
        found = (
            [Path(m) for m in glob.glob(raw, recursive=True)]
            if has_glob
            else (_expand_directory(candidate) if candidate.is_dir() else [candidate])
        )
    elif has_glob:
        found = [Path(m) for m in glob.glob(str(project.root / raw), recursive=True)]
    else:
        direct = project.root / raw
        found = _expand_directory(direct) if direct.is_dir() else [direct]

    files = [p for p in found if p.is_file()]
    for path in files:
        _refuse_sensitive(pattern, path)
    return sorted(files)


def collect(
    project: Project,
    *,
    extra: list[str] | None = None,
    enabled: bool = True,
    max_bytes: int | None = None,
) -> Bundle:
    """Read the configured sources plus any `extra` paths, under the configured caps.

    `max_bytes` raises the per-file cap for this call only — `--context-bytes` on the CLI. The
    total cap rises with it, because a per-file cap you cannot actually use is not a cap.
    """
    if not enabled:
        return Bundle()

    sources, limits = configured(project)
    if max_bytes and max_bytes > 0:
        limits = dict(limits)
        limits["max_bytes_per_file"] = max_bytes
        limits["max_total_bytes"] = max(limits["max_total_bytes"], max_bytes * 2)
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
            rel = label(project, path)

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
            # Truncation is decided by the allowance, not by the length after rstrip — otherwise
            # a file ending in a newline reports itself as cut by one character.
            truncated = total > allowance
            kept, dropped = excerpt(text, allowance)
            kept = kept.rstrip()
            budget -= len(kept)
            bundle.files.append(
                ContextFile(
                    rel=rel, text=kept, bytes_total=total,
                    truncated=truncated, dropped=dropped,
                )
            )

    return bundle
