from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from hiraku.errors import HirakuError
from hiraku.git import top_level

CONFIG_NAME = ".hiraku.yml"


@dataclass(frozen=True)
class ProjectConfig:
    root: Path
    experiments_dir: Path
    experiment_template: Path
    policies: dict[str, Path]
    tracking_backend: str
    table_path: Path


def resolve_project_path(root: Path, value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise HirakuError(f"Configuration field '{field}' must be a non-empty relative path.")
    candidate = Path(value)
    if candidate.is_absolute():
        raise HirakuError(f"Configuration field '{field}' must be relative to the project root.")
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise HirakuError(f"Configuration field '{field}' escapes the project root.")
    return resolved


def find_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent
    for directory in (current, *current.parents):
        if (directory / CONFIG_NAME).is_file():
            try:
                git_root = top_level(directory)
            except HirakuError as exc:
                raise HirakuError("The Hiraku project must be inside a Git worktree.") from exc
            if git_root != directory:
                raise HirakuError(".hiraku.yml must be located at the Git repository root.")
            return directory
    raise HirakuError(f"No {CONFIG_NAME} found in this directory or its parents.")


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HirakuError(f"Configuration field '{field}' must be a mapping.")
    return value


def load_config(start: Path | None = None) -> ProjectConfig:
    root = find_project_root(start)
    config_path = root / CONFIG_NAME
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise HirakuError(f"Could not read {config_path}: {exc}") from exc
    data = _mapping(raw, "root")
    policies_raw = _mapping(data.get("policies"), "policies")
    tracking = _mapping(data.get("tracking"), "tracking")
    backend = tracking.get("backend")
    if backend != "table":
        raise HirakuError("impl1 supports only tracking.backend: table.")
    table_path = resolve_project_path(root, tracking.get("path"), "tracking.path")
    if table_path.suffix.lower() not in {".csv", ".tsv"}:
        raise HirakuError("tracking.path must end in .csv or .tsv.")
    policies = {
        mode: resolve_project_path(root, policies_raw.get(mode), f"policies.{mode}")
        for mode in ("guided", "auto")
    }
    return ProjectConfig(
        root=root,
        experiments_dir=resolve_project_path(root, data.get("experiments_dir"), "experiments_dir"),
        experiment_template=resolve_project_path(
            root, data.get("experiment_template"), "experiment_template"
        ),
        policies=policies,
        tracking_backend=backend,
        table_path=table_path,
    )
