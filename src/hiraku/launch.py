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
    "HIRAKU_STATE_PATHS",
}


# Exit status of a launch refused because the dispatcher would run another commit than
# the one asked for (the record's own, or `--commit`).
COMMIT_MISMATCH_EXIT = 86
COMMIT_CHECK_OPT_OUT_ENV = "HIRAKU_NO_COMMIT_CHECK"

# Runs inside whatever the dispatcher prepared (its checkout, its job, its host), just
# before the command, so it sees the commit that would really run: the dispatcher's
# `HIRAKU_COMMIT` (the launch contract), else `git rev-parse HEAD` where it runs. A
# dispatcher that ignores `--commit` (`launch.sh` runs the current checkout) is caught
# here instead of silently running, and attributing to the record, other code.
# Arguments: expected commit, then the command.
_COMMIT_GUARD = r"""
expected=$1
shift
actual=${HIRAKU_COMMIT:-}
source="the dispatcher's HIRAKU_COMMIT"
if [ -z "$actual" ] && command -v git >/dev/null 2>&1; then
  actual=$(git rev-parse --verify -q 'HEAD^{commit}' 2>/dev/null || true)
  source="git HEAD in $(pwd)"
fi
if [ -z "$actual" ]; then
  echo "hiraku: warning: cannot tell which commit runs here" \
    "(no HIRAKU_COMMIT, no git checkout); expected $expected" >&2
else
  case "$expected" in
    "$actual"*) ;;
    *)
      case "$actual" in
        "$expected"*) ;;
        *)
          echo "hiraku: refusing to run: the launch asks for commit $expected," \
            "but this would run $actual ($source)." >&2
          echo "hiraku: the dispatcher does not check out the requested commit" \
            "(launch.sh runs the current checkout). Use a dispatcher that does" \
            "(--dispatch worktree or slurm; see hiraku-agent check-dispatch)," \
            "check out $expected first, or pass --no-commit-check to run this" \
            "checkout anyway." >&2
          exit 86
          ;;
      esac
      ;;
  esac
  if [ "${HIRAKU_GIT_STATE:-}" = dirty ]; then
    echo "hiraku: warning: the checkout has uncommitted changes," \
      "so the code that runs is not exactly $expected." >&2
  fi
fi
exec "$@"
"""


def resolve_commit(config: HirakuConfig, ref: str) -> str:
    """`ref` as a full commit hash in the project's repository.

    Outside a Git repository (or without Git) `ref` is returned unchanged and the guard
    compares it as given. Inside one, a ref that does not resolve is an error: no
    dispatcher can run it.
    """
    try:
        inside = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=config.root,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return ref
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return ref
    resolved = subprocess.run(
        ["git", "rev-parse", "--verify", "-q", f"{ref}^{{commit}}"],
        cwd=config.root,
        text=True,
        capture_output=True,
        check=False,
    )
    if resolved.returncode != 0 or not resolved.stdout.strip():
        raise LaunchError(
            f"Commit {ref!r} is not in this repository, so it cannot be launched "
            f"here. Fetch it (the record's branch) first."
        )
    return resolved.stdout.strip()


def guarded(command: Sequence[str], expected: str) -> list[str]:
    """`command` preceded by the commit guard: it runs only if the commit the
    dispatcher prepared is `expected`."""
    return ["/bin/sh", "-c", _COMMIT_GUARD, "hiraku-commit-check", expected, *command]


def state_paths(config: HirakuConfig) -> list[str]:
    """Paths of Hiraku's own project state, relative to the project root.

    The configuration file, the state directory (records, contexts, agent logs, ...)
    and the default CSV tracking file with its lock. They record what was designed,
    run and found, not the code that ran, so a dispatcher that reports whether the
    tree is clean leaves them out: committing a new record does not change what a
    launch runs, and neither does an unsaved one.
    """
    root = config.root.resolve()
    paths = [config.path.resolve(), config.hiraku_dir.resolve()]
    paths += [root / "runs.csv", root / "runs.csv.lock"]
    out: list[str] = []
    for path in paths:
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if relative and relative != "." and relative not in out:
            out.append(relative)
    return out


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
    verify_commit: bool = True,
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
        verify_commit: When a commit is launched (the record's own, or ``commit``),
            refuse to run the command unless the dispatcher really prepared that
            commit: a guard runs just before the command, where the dispatcher put
            it, and compares ``HIRAKU_COMMIT`` (else ``git rev-parse HEAD`` there)
            with it, exiting with ``COMMIT_MISMATCH_EXIT`` on a mismatch. So a
            dispatcher that ignores ``--commit`` (``launch.sh``) cannot run, and
            attribute to the record, other code. ``HIRAKU_NO_COMMIT_CHECK=1`` also
            turns it off.
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
    if selected_commit is not None:
        selected_commit = resolve_commit(selected, selected_commit)
        if verify_commit and not os.environ.get(COMMIT_CHECK_OPT_OUT_ENV):
            command = guarded(command, selected_commit)
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
            "HIRAKU_STATE_PATHS": "\n".join(state_paths(selected)),
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
        if result.returncode == COMMIT_MISMATCH_EXIT:
            message = (
                f"Not launched: dispatcher {dispatcher} would not run commit "
                f"{selected_commit}"
            )
        if detail:
            message += f": {detail}"
        raise LaunchError(message) from error
    return result
