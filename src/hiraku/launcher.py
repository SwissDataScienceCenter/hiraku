from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from hiraku.config import ProjectConfig
from hiraku.errors import HirakuError
from hiraku.git import current_branch, ensure_clean, head_commit
from hiraku.records import EXPERIMENT_ID_PATTERN, load_experiment


@dataclass(frozen=True)
class Entrypoint:
    command: list[str]
    cwd: Path
    env: dict[str, str]


def parse_target(target: str) -> tuple[str, str]:
    if target.count(":") != 1:
        raise HirakuError("Launch target must have the form <experiment>:<entrypoint>.")
    experiment_id, entrypoint_name = target.split(":", 1)
    if not EXPERIMENT_ID_PATTERN.fullmatch(experiment_id):
        raise HirakuError(f"Invalid experiment ID in launch target: {experiment_id}")
    if not EXPERIMENT_ID_PATTERN.fullmatch(entrypoint_name):
        raise HirakuError(f"Invalid entrypoint name: {entrypoint_name}")
    return experiment_id, entrypoint_name


def _mapping(value: Any, description: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HirakuError(f"{description} must be a mapping.")
    return value


def load_entrypoint(config: ProjectConfig, experiment_id: str, entrypoint_name: str) -> Entrypoint:
    manifest_path = config.experiments_dir / experiment_id / "launch.yml"
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HirakuError(f"Launch manifest does not exist: {manifest_path}") from exc
    except (OSError, yaml.YAMLError) as exc:
        raise HirakuError(f"Could not read launch manifest {manifest_path}: {exc}") from exc
    manifest = _mapping(raw, "Launch manifest")
    entrypoints = _mapping(manifest.get("entrypoints"), "launch.yml entrypoints")
    if entrypoint_name not in entrypoints:
        raise HirakuError(
            f"Entrypoint '{entrypoint_name}' is not defined for experiment '{experiment_id}'."
        )
    definition = _mapping(entrypoints[entrypoint_name], f"Entrypoint '{entrypoint_name}'")
    unknown = set(definition) - {"command", "cwd", "env"}
    if unknown:
        raise HirakuError(
            f"Entrypoint '{entrypoint_name}' has unknown fields: {', '.join(sorted(unknown))}"
        )
    command = definition.get("command")
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(token, str) or not token for token in command)
    ):
        raise HirakuError(f"Entrypoint '{entrypoint_name}' command must be a non-empty argv list.")

    cwd_value = definition.get("cwd", ".")
    if not isinstance(cwd_value, str) or not cwd_value:
        raise HirakuError(f"Entrypoint '{entrypoint_name}' cwd must be a relative path string.")
    cwd_path = Path(cwd_value)
    if cwd_path.is_absolute():
        raise HirakuError(f"Entrypoint '{entrypoint_name}' cwd must be project-relative.")
    resolved_cwd = (config.root / cwd_path).resolve()
    if not resolved_cwd.is_relative_to(config.root) or not resolved_cwd.is_dir():
        raise HirakuError(
            f"Entrypoint '{entrypoint_name}' cwd must resolve to a directory inside the project."
        )

    env_value = definition.get("env", {})
    env_raw = _mapping(env_value, f"Entrypoint '{entrypoint_name}' env")
    env: dict[str, str] = {}
    for key, value in env_raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise HirakuError(
                f"Entrypoint '{entrypoint_name}' environment must map strings to strings."
            )
        if key.startswith("HIRAKU_"):
            raise HirakuError(
                f"Entrypoint '{entrypoint_name}' cannot override reserved variable {key}."
            )
        if not key or "=" in key or "\0" in key or "\0" in value:
            raise HirakuError(
                f"Entrypoint '{entrypoint_name}' contains an invalid environment value."
            )
        env[key] = value
    return Entrypoint(command=list(command), cwd=resolved_cwd, env=env)


def launch(config: ProjectConfig, target: str) -> int:
    experiment_id, entrypoint_name = parse_target(target)
    experiment = load_experiment(config, experiment_id)
    ensure_clean(config.root)
    branch = current_branch(config.root)
    if branch != experiment.branch:
        raise HirakuError(
            f"Experiment '{experiment_id}' requires branch '{experiment.branch}', "
            f"but '{branch}' is checked out."
        )
    commit = head_commit(config.root)
    entrypoint = load_entrypoint(config, experiment_id, entrypoint_name)
    child_env = os.environ.copy()
    child_env.update(entrypoint.env)
    child_env.update(
        {
            "HIRAKU_EXPERIMENT_ID": experiment.id,
            "HIRAKU_GIT_COMMIT": commit,
            "HIRAKU_TRACKING_BACKEND": config.tracking_backend,
            "HIRAKU_TABLE_PATH": str(config.table_path),
        }
    )
    try:
        result = subprocess.run(entrypoint.command, cwd=entrypoint.cwd, env=child_env, check=False)
    except FileNotFoundError as exc:
        raise HirakuError(f"Entrypoint executable was not found: {entrypoint.command[0]}") from exc
    except OSError as exc:
        raise HirakuError(f"Could not launch entrypoint: {exc}") from exc
    return result.returncode if result.returncode >= 0 else 128 + abs(result.returncode)
