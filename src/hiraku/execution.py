"""Foreground command execution with record and run attribution."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from .config import HirakuConfig
from .errors import ExecutionError, GitError, NoActiveRecordError, ValidationError
from .git import GitRepository
from .record import load_record
from .validation import validate_command


def execute_command(
    command: Sequence[str] | None = None,
    *,
    record_id: str | None = None,
    dispatch: str | None = None,
    env: Mapping[str, str] | None = None,
    capture_output: bool = False,
    config: HirakuConfig | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command in the current project and return when it exits."""
    selected = config or HirakuConfig.discover()
    selected_record = record_id or selected.active_record_id
    if selected_record is None:
        raise NoActiveRecordError("Select a record with --record or hiraku record set")
    record = load_record(selected_record, config=selected)
    if isinstance(command, str | bytes):
        raise ValidationError("command must be an argv sequence, not a shell string")
    inherited = record.resolve_command(config=selected)
    argv = validate_command(list(command) if command is not None else inherited)
    if argv is None:
        raise ValidationError("No default command; supply a command after --")

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
        "HIRAKU_ENTRYPOINT",
    }
    if reserved.intersection(additions):
        raise ValidationError("Environment cannot override reserved run attribution")

    tracking_spec = record.tracking_backend_spec(config=selected)
    if tracking_spec.get("type") == "wandb":
        wandb_project = tracking_spec.get("project")
        if (
            isinstance(wandb_project, str)
            and wandb_project
            and "WANDB_PROJECT" not in additions
            and "WANDB_PROJECT" not in os.environ
        ):
            additions["WANDB_PROJECT"] = wandb_project

    commit = ""
    git_state = "unavailable"
    try:
        repository = GitRepository(selected)
        commit = repository.resolve("HEAD")
        git_state = (
            "dirty"
            if repository._git(
                "status", "--porcelain", "--untracked-files=normal"
            ).stdout
            else "clean"
        )
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
