from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from .errors import HirakuError

RESERVED_ENV = {
    "HIRAKU_RUN_ID",
    "HIRAKU_GOAL_ID",
    "HIRAKU_EXPERIMENT_ID",
    "HIRAKU_EXPERIMENT_KIND",
    "HIRAKU_GIT_COMMIT",
    "HIRAKU_METRICS_PATH",
    "HIRAKU_PROJECT_ROOT",
    "HIRAKU_STATE_PATH",
    "HIRAKU_HYPOTHESIS",
}


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HirakuError(f"Launch manifest does not exist: {path}") from exc
    except (OSError, yaml.YAMLError) as exc:
        raise HirakuError(f"Cannot read launch manifest {path}: {exc}") from exc
    return validate_manifest(raw)


def validate_manifest(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get("entrypoints"), dict):
        raise HirakuError("Launch manifest must contain an 'entrypoints' mapping.")
    if not raw["entrypoints"]:
        raise HirakuError("Launch manifest must define at least one entrypoint.")
    clean: dict[str, Any] = {"entrypoints": {}}
    for name, definition in raw["entrypoints"].items():
        if not isinstance(name, str) or not name:
            raise HirakuError("Entrypoint names must be non-empty strings.")
        if not isinstance(definition, dict):
            raise HirakuError(f"Entrypoint '{name}' must be a mapping.")
        command = definition.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(v, str) for v in command):
            raise HirakuError(f"Entrypoint '{name}' command must be a non-empty string list.")
        cwd = definition.get("cwd", ".")
        pure = PurePosixPath(cwd)
        if not isinstance(cwd, str) or pure.is_absolute() or ".." in pure.parts:
            raise HirakuError(f"Entrypoint '{name}' cwd must stay inside the project.")
        env = definition.get("env", {})
        if not isinstance(env, dict) or not all(
            isinstance(k, str) and isinstance(v, (str, int, float, bool)) for k, v in env.items()
        ):
            raise HirakuError(f"Entrypoint '{name}' env must contain scalar values.")
        overlap = RESERVED_ENV.intersection(env)
        if overlap:
            raise HirakuError(f"Entrypoint '{name}' overrides reserved environment: {sorted(overlap)}")
        clean["entrypoints"][name] = {
            "command": command,
            "cwd": cwd,
            "env": {key: str(value) for key, value in env.items()},
        }
    auto = raw.get("auto")
    if auto is not None:
        if not isinstance(auto, dict) or not isinstance(auto.get("implement"), dict):
            raise HirakuError("Manifest 'auto.implement' must be a command definition.")
        definition = auto["implement"]
        command = definition.get("command")
        if not isinstance(command, list) or not command or not all(
            isinstance(value, str) for value in command
        ):
            raise HirakuError("Manifest 'auto.implement.command' must be a non-empty string list.")
        cwd = definition.get("cwd", ".")
        pure = PurePosixPath(cwd)
        if not isinstance(cwd, str) or pure.is_absolute() or ".." in pure.parts:
            raise HirakuError("Manifest 'auto.implement.cwd' must stay inside the project.")
        env = definition.get("env", {})
        if not isinstance(env, dict) or not all(
            isinstance(key, str) and isinstance(value, (str, int, float, bool))
            for key, value in env.items()
        ):
            raise HirakuError("Manifest 'auto.implement.env' must contain scalar values.")
        overlap = RESERVED_ENV.intersection(env)
        if overlap:
            raise HirakuError(f"Auto implementation overrides reserved environment: {sorted(overlap)}")
        allowed = auto.get("allowed_entrypoints")
        if not isinstance(allowed, list) or not allowed or not all(
            isinstance(name, str) and name in clean["entrypoints"] for name in allowed
        ):
            raise HirakuError(
                "Manifest 'auto.allowed_entrypoints' must name one or more defined entrypoints."
            )
        clean["auto"] = {
            "allowed_entrypoints": allowed,
            "implement": {
                "command": command,
                "cwd": cwd,
                "env": {key: str(value) for key, value in env.items()},
            }
        }
    return clean
