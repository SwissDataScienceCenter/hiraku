from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from hiraku.config import ProjectConfig
from hiraku.errors import HirakuError
from hiraku.git import validate_branch_name

EXPERIMENT_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True)
class Experiment:
    id: str
    branch: str
    mode: str
    metadata: dict[str, Any]
    path: Path
    raw: str


def validate_experiment_id(experiment_id: str) -> None:
    if not EXPERIMENT_ID_PATTERN.fullmatch(experiment_id):
        raise HirakuError(
            "Experiment ID must be a lowercase slug containing letters, digits, and single hyphens."
        )


def parse_record(path: Path, expected_id: str | None = None) -> Experiment:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HirakuError(f"Could not read experiment record {path}: {exc}") from exc
    lines = raw.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise HirakuError(f"Experiment record {path} must begin with YAML front matter.")
    closing = next(
        (index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"),
        None,
    )
    if closing is None:
        raise HirakuError(f"Experiment record {path} has unterminated YAML front matter.")
    try:
        metadata = yaml.safe_load("".join(lines[1:closing]))
    except yaml.YAMLError as exc:
        raise HirakuError(f"Invalid YAML front matter in {path}: {exc}") from exc
    if not isinstance(metadata, dict):
        raise HirakuError(f"Experiment record {path} front matter must be a mapping.")
    for field in ("id", "branch", "mode"):
        if not isinstance(metadata.get(field), str) or not metadata[field]:
            raise HirakuError(f"Experiment record {path} requires string field '{field}'.")
    experiment_id = metadata["id"]
    validate_experiment_id(experiment_id)
    if expected_id is not None and experiment_id != expected_id:
        raise HirakuError(
            f"Experiment record ID '{experiment_id}' does not match directory '{expected_id}'."
        )
    if metadata["mode"] not in {"guided", "auto"}:
        raise HirakuError(f"Experiment record {path} has invalid mode '{metadata['mode']}'.")
    return Experiment(
        id=experiment_id,
        branch=metadata["branch"],
        mode=metadata["mode"],
        metadata=metadata,
        path=path,
        raw=raw,
    )


def load_experiment(config: ProjectConfig, experiment_id: str) -> Experiment:
    validate_experiment_id(experiment_id)
    path = config.experiments_dir / experiment_id / "experiment.md"
    if not path.is_file():
        raise HirakuError(f"Experiment '{experiment_id}' does not exist.")
    experiment = parse_record(path, expected_id=experiment_id)
    validate_branch_name(experiment.branch, config.root)
    return experiment


def list_experiments(config: ProjectConfig) -> list[Experiment]:
    if not config.experiments_dir.exists():
        return []
    experiments = []
    for path in sorted(config.experiments_dir.glob("*/experiment.md")):
        experiment = parse_record(path, expected_id=path.parent.name)
        validate_branch_name(experiment.branch, config.root)
        experiments.append(experiment)
    return experiments
