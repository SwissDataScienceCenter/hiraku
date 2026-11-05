"""Foreground command execution with record and run attribution."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .errors import ExecutionError, GitError, NoActiveRecordError, ValidationError
from .git import GitRepository
from .project import Project


def validate_command(value: Any) -> list[str] | None:
    """Validate command configuration as a literal argv sequence."""
    if value is None:
        return None
    if isinstance(value, str | bytes) or not isinstance(value, list | tuple):
        raise ValidationError("command must be an argv sequence")
    command = list(value)
    if not command or not isinstance(command[0], str) or not command[0]:
        raise ValidationError("command executable must be a non-empty string")
    if any(not isinstance(argument, str) or "\x00" in argument for argument in command):
        raise ValidationError("command arguments must be strings without NUL bytes")
    return command


def execute_command(
    command: Sequence[str],
    *,
    record_id: str | None = None,
    dispatch: str | None = None,
    env: Mapping[str, str] | None = None,
    capture_output: bool = False,
    project: Project | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command in the current project and return when it exits."""
    selected = project or Project.discover()
    selected_record = record_id or selected.active_record_id
    if selected_record is None:
        raise NoActiveRecordError("Select a record with --record or hiraku record set")
    record = selected.vault.find(selected_record)
    if isinstance(command, str | bytes):
        raise ValidationError("command must be an argv sequence, not a shell string")
    if command is None or isinstance(command, str | bytes):
        raise ValidationError("command must be an argv sequence")
    argv = validate_command(list(command))
    if argv is None:
        raise ValidationError("command must be a non-empty argv sequence")

    additions = dict(env or {})
    if any(
        not isinstance(key, str)
        or not key
        or "=" in key
        or "\x00" in key
        or not isinstance(value, str)
        or "\x00" in value
        for key, value in additions.items()
    ):
        raise ValidationError("Environment additions must map strings to strings")
    reserved = {
        "HIRAKU_RECORD_ID",
        "HIRAKU_RECORD_FILE",
        "HIRAKU_ROOT",
        "HIRAKU_TRACKING_ROOT",
        "HIRAKU_COMMIT",
        "HIRAKU_GIT_STATE",
        "HIRAKU_SOURCE_DIGEST",
    }
    if reserved.intersection(additions):
        raise ValidationError("Environment cannot override reserved run attribution")

    raw_tracking_spec = selected.tracking_backend
    tracking_spec = (
        {"type": raw_tracking_spec}
        if isinstance(raw_tracking_spec, str)
        else raw_tracking_spec
    )
    tracking_type = tracking_spec.get("type", tracking_spec.get("name"))
    if tracking_type == "wandb":
        wandb_project = tracking_spec.get("project")
        if (
            isinstance(wandb_project, str)
            and wandb_project
            and "WANDB_PROJECT" not in additions
            and "WANDB_PROJECT" not in os.environ
        ):
            additions["WANDB_PROJECT"] = wandb_project
    elif tracking_type == "mlflow":
        from .tracking import TrackingBackendFactory

        backend = TrackingBackendFactory(selected).create(tracking_spec)
        if (
            "MLFLOW_TRACKING_URI" not in additions
            and "MLFLOW_TRACKING_URI" not in os.environ
        ):
            additions["MLFLOW_TRACKING_URI"] = backend.tracking_uri
        if (
            "MLFLOW_EXPERIMENT_NAME" not in additions
            and "MLFLOW_EXPERIMENT_NAME" not in os.environ
        ):
            additions["MLFLOW_EXPERIMENT_NAME"] = (
                backend.experiment_name or selected.root.name
            )

    commit = ""
    git_state = "unavailable"
    try:
        repository = GitRepository(selected.root)
        commit = repository.resolve("HEAD")
        git_state = "dirty" if repository.is_dirty else "clean"
    except GitError:
        pass

    environment = os.environ.copy()
    for name in reserved:
        environment.pop(name, None)
    environment.update(additions)
    environment.update(
        {
            "HIRAKU_RECORD_ID": record.id,
            "HIRAKU_RECORD_FILE": str(record.path),
            "HIRAKU_ROOT": str(selected.root),
            "HIRAKU_TRACKING_ROOT": str(selected.root),
            "HIRAKU_COMMIT": commit,
            "HIRAKU_GIT_STATE": git_state,
        }
    )

    dispatcher_name = dispatch if dispatch is not None else selected.default_dispatch
    if dispatcher_name:
        if (
            not isinstance(dispatcher_name, str)
            or dispatcher_name in {".", ".."}
            or "/" in dispatcher_name
            or "\\" in dispatcher_name
            or "\x00" in dispatcher_name
        ):
            raise ValidationError(f"Invalid dispatcher name: {dispatcher_name!r}")
        filename = (
            dispatcher_name if Path(dispatcher_name).suffix else dispatcher_name + ".sh"
        )
        dispatcher_path = selected.dispatch_dir / filename
        if dispatch is not None and not dispatcher_path.is_file():
            raise ExecutionError(f"Dispatcher does not exist: {dispatcher_path}")
        if dispatcher_path.is_file():
            argv = [str(dispatcher_path), "--", *argv]

    try:
        return subprocess.run(
            argv,
            cwd=selected.root,
            env=environment,
            check=False,
            capture_output=capture_output,
            text=True,
        )
    except OSError as exc:
        raise ExecutionError(f"Could not execute command {argv[0]!r}: {exc}") from exc
