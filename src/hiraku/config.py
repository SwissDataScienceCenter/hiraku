from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from .errors import HirakuError


@dataclass(frozen=True)
class Config:
    root: Path
    state_path: Path
    reports_dir: Path
    metrics_dir: Path


def find_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        if (directory / ".hiraku.yml").is_file():
            return directory
    raise HirakuError("No .hiraku.yml found in this directory or its parents. Run 'hiraku init'.")


def load_config(start: Path | None = None) -> Config:
    injected_root = os.environ.get("HIRAKU_PROJECT_ROOT")
    injected_state = os.environ.get("HIRAKU_STATE_PATH")
    if injected_root and injected_state:
        root = Path(injected_root).resolve()
        return Config(
            root=root,
            state_path=Path(injected_state).resolve(),
            reports_dir=root / ".hiraku/reports",
            metrics_dir=root / ".hiraku/run-metrics",
        )
    root = find_root(start)
    path = root / ".hiraku.yml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise HirakuError(f"Cannot read {path}: {exc}") from exc
    state = raw.get("state", {})
    reports = raw.get("reports", {})
    return Config(
        root=root,
        state_path=root / state.get("path", ".hiraku/state.db"),
        reports_dir=root / reports.get("path", ".hiraku/reports"),
        metrics_dir=root / ".hiraku/run-metrics",
    )
