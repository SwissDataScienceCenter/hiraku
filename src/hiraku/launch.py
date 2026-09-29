"""Entrypoint validation, argument rendering, and dispatch execution."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import HirakuConfig
from .errors import LaunchError, ValidationError

if TYPE_CHECKING:
    from .record import ArgumentValue, Entrypoint

_ATTRIBUTION_KEYS = {
    "HIRAKU_RECORD_ID",
    "HIRAKU_RECORD_FILE",
    "HIRAKU_COMMIT",
    "HIRAKU_GIT_STATE",
    "HIRAKU_ROOT",
    "HIRAKU_ENTRYPOINT",
}


def render_arguments(arguments: Mapping[str, ArgumentValue]) -> tuple[str, ...]:
    """Render named arguments as deterministic GNU-style flags.

    Example:
        ``render_arguments({"learning_rate": 0.1, "verbose": True})``
        returns ``("--learning-rate", "0.1", "--verbose")``.
    """
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
    entrypoint: Entrypoint | Mapping[str, Any],
    *,
    name: str | None = None,
    args: Mapping[str, ArgumentValue] | None = None,
    record_id: str,
    commit: str | None = None,
    dispatch: str | None = None,
    env: Mapping[str, str] | None = None,
    capture_output: bool = False,
    check: bool = True,
    config: HirakuConfig | None = None,
) -> subprocess.CompletedProcess[str]:
    """
    Launch an entrypoint through a project dispatcher.

    Args:
        entrypoint: Entrypoint model or mapping accepted by ``model_validate``.
        name: Logical entrypoint name for attribution.
        args: Call-time arguments overriding entrypoint defaults.
        record_id: Required record attribution.
        commit: Optional Git ref supplied to the dispatcher.
        dispatch: Optional dispatcher script override.
        env: Additional environment variables.
        capture_output: Capture child output in the result instead of streaming it.
        check: Raise on non-zero child exit when true.
        config: Project configuration; discovered when omitted.

    Returns:
        The completed subprocess result.

    Raises:
        hiraku.errors.ValidationError: If the entrypoint, arguments, or attribution
            is invalid.
        hiraku.errors.LaunchError: If the command cannot be started.

    Example:
        ``launch({"command": ["python", "evaluate.py"]}, record_id="demo")``
    Warning:
        Commands are executed from the selected project context and do not use
        shell expansion.

    """
    from .record import Entrypoint, load_record

    entrypoint = Entrypoint.model_validate(entrypoint)
    selected = config or HirakuConfig.discover()
    record = load_record(record_id, config=selected)
    selected_commit = commit
    if selected_commit is None:
        selected_commit = record.commit
    defaults = dict(entrypoint.args)
    if args is not None:
        if not isinstance(args, Mapping):
            raise ValidationError("Call-time args must be a mapping")
        defaults.update(args)
    command = [*entrypoint.command, *render_arguments(defaults)]
    dispatcher = _dispatcher(selected, dispatch)
    dispatcher_arguments: list[str] = [str(dispatcher)]
    if selected_commit is not None:
        dispatcher_arguments.extend(("--commit", selected_commit))
    dispatcher_arguments.extend(("--", *command))
    additions = dict(env or {})
    if any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in additions.items()
    ):
        raise ValidationError("Environment additions must map strings to strings")
    conflicts = _ATTRIBUTION_KEYS.intersection(additions)
    if conflicts:
        raise ValidationError(
            f"Environment cannot override reserved launch context: {sorted(conflicts)}"
        )
    process_env = os.environ.copy()
    process_env.update(additions)
    process_env.update(
        {
            "HIRAKU_RECORD_ID": record.id,
            "HIRAKU_RECORD_FILE": str(record.path.resolve()),
            "HIRAKU_COMMIT": "",
            "HIRAKU_GIT_STATE": "unavailable",
            "HIRAKU_ROOT": str(selected.root.resolve()),
            "HIRAKU_ENTRYPOINT": name or "",
        }
    )
    try:
        result = subprocess.run(
            dispatcher_arguments,
            cwd=selected.root,
            env=process_env,
            text=True,
            capture_output=capture_output,
            check=False,
        )
    except Exception as exc:
        if isinstance(exc, LaunchError | ValidationError):
            raise
        raise LaunchError(
            f"Launch through dispatcher {dispatcher} failed: {exc}"
        ) from exc
    if check and result.returncode != 0:
        detail = (result.stderr or "").strip() or (result.stdout or "").strip()
        error = subprocess.CalledProcessError(
            result.returncode, result.args, output=result.stdout, stderr=result.stderr
        )
        message = f"Dispatcher {dispatcher} exited with status {result.returncode}"
        if detail:
            message += f": {detail}"
        raise LaunchError(message) from error
    return result
