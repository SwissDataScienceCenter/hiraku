"""Entrypoint validation, argument rendering, and dispatch execution."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

from .config import HirakuConfig
from .errors import LaunchError, ValidationError

if TYPE_CHECKING:
    from .data import ArgumentValue, Entrypoint

_ATTRIBUTION_KEYS = {
    "HIRAKU_TASK_ID",
    "HIRAKU_TASK_FILE",
    "HIRAKU_EXPERIMENT_ID",
    "HIRAKU_EXPERIMENT_FILE",
    "HIRAKU_COMMIT",
    "HIRAKU_ENTRYPOINT",
}


def render_arguments(arguments: Mapping[str, ArgumentValue]) -> tuple[str, ...]:
    """Render a named argument mapping as deterministic GNU-style flags."""
    if not isinstance(arguments, Mapping):
        raise ValidationError("Arguments must be a mapping")
    rendered: list[str] = []
    for key, value in arguments.items():
        if not isinstance(key, str) or not key:
            raise ValidationError(f"Argument key must be a non-empty string: {key!r}")
        flag = f"--{key.replace('_', '-')}"
        if value is False or value is None:
            continue
        if value is True:
            rendered.append(flag)
        elif isinstance(value, str | int | float | os.PathLike) and not isinstance(
            value, bool
        ):
            rendered.extend(
                (
                    flag,
                    os.fspath(value) if isinstance(value, os.PathLike) else str(value),
                )
            )
        elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
            for item in value:
                if isinstance(item, bool) or not isinstance(
                    item, str | int | float | os.PathLike
                ):
                    raise ValidationError(
                        f"Unsupported value for argument {key!r}: {item!r}"
                    )
                rendered.extend(
                    (
                        flag,
                        os.fspath(item) if isinstance(item, os.PathLike) else str(item),
                    )
                )
        else:
            raise ValidationError(f"Unsupported value for argument {key!r}: {value!r}")
    return tuple(rendered)


def _dispatcher(config: HirakuConfig, dispatch: str | None) -> Path:
    name = dispatch or config.default_dispatch
    if not isinstance(name, str) or not name:
        raise ValidationError("Dispatch name must be a non-empty string")
    if "/" in name or "\\" in name or name in {".", ".."}:
        raise ValidationError(f"Invalid dispatcher name: {name!r}")
    if not Path(name).suffix:
        name += ".sh"
    path = config.dispatch_dir / name
    if not path.is_file():
        raise LaunchError(f"Dispatcher not found: {path}")
    return path


def launch(
    entrypoint: Entrypoint,
    *,
    name: str | None = None,
    args: Mapping[str, ArgumentValue] | None = None,
    task_id: str | None = None,
    experiment_id: str | None = None,
    commit: str | None = None,
    dispatch: str | None = None,
    worktree: bool | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = True,
    config: HirakuConfig | None = None,
) -> subprocess.CompletedProcess[str]:
    """Launch an entrypoint through a project dispatcher."""
    from .data import load_experiment, load_task
    from .git import GitRepository

    selected = config or HirakuConfig.discover()
    if experiment_id is not None and task_id is None:
        raise ValidationError("experiment_id requires an explicit task_id")
    task = load_task(task_id, config=selected) if task_id is not None else None
    experiment = (
        load_experiment(experiment_id, task=task, config=selected)
        if experiment_id is not None
        else None
    )
    defaults = dict(entrypoint.args)
    if args is not None:
        if not isinstance(args, Mapping):
            raise ValidationError("Call-time args must be a mapping")
        defaults.update(args)
    command = [*entrypoint.command, *render_arguments(defaults)]
    dispatcher = _dispatcher(selected, dispatch)
    injected: dict[str, str] = {}
    if name is not None:
        injected["HIRAKU_ENTRYPOINT"] = name
    if task is not None:
        injected.update(HIRAKU_TASK_ID=task.id, HIRAKU_TASK_FILE=str(task.path))
    if experiment is not None:
        injected.update(
            HIRAKU_EXPERIMENT_ID=experiment.id,
            HIRAKU_EXPERIMENT_FILE=str(experiment.path),
        )
    repository = GitRepository(selected) if commit is not None or worktree else None
    resolved_commit = (
        repository.resolve(commit) if repository is not None and commit else None
    )
    if worktree and resolved_commit is None:
        raise ValidationError("worktree=True requires an explicit commit")
    if resolved_commit is not None:
        injected["HIRAKU_COMMIT"] = resolved_commit
    additions = dict(env or {})
    if any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in additions.items()
    ):
        raise ValidationError("Environment additions must map strings to strings")
    conflicts = _ATTRIBUTION_KEYS.intersection(additions).intersection(injected)
    if conflicts:
        raise ValidationError(
            f"Environment cannot override injected attribution: {sorted(conflicts)}"
        )
    process_env = os.environ.copy()
    process_env.update(additions)
    process_env.update(injected)
    context = (
        repository.temporary_worktree(resolved_commit)
        if resolved_commit
        else nullcontext(selected.root)
    )
    try:
        with context as working_directory:
            if resolved_commit is not None and repository is not None:
                project_relative = selected.root.resolve().relative_to(repository.root)
                working_directory = working_directory / project_relative
            result = subprocess.run(
                [str(dispatcher), "--", *command],
                cwd=working_directory,
                env=process_env,
                text=True,
                capture_output=True,
                check=False,
            )
    except Exception as exc:
        if isinstance(exc, LaunchError | ValidationError):
            raise
        raise LaunchError(
            f"Launch through dispatcher {dispatcher} failed: {exc}"
        ) from exc
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        error = subprocess.CalledProcessError(
            result.returncode, result.args, output=result.stdout, stderr=result.stderr
        )
        raise LaunchError(
            f"Dispatcher {dispatcher} exited with status {result.returncode}: {detail}"
        ) from error
    return result
