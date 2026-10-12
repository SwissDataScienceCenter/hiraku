"""One writer at a time per project.

Every workflow re-reads a record before writing it, but that is not atomic: a `loop`
step and a manual call (or two terminals) can each pass their checks and then both
write — two `triage`s deciding the same node, or a `design` adding children to a node a
`revise` is rewriting. Each writing workflow therefore holds this lock from its checks
to its last write, agent call included. A second writer fails at once and says who
holds the lock, rather than waiting minutes behind an agent call or racing it.

`--dry-run` and `--no-apply` write nothing and take no lock. The lock is re-entrant
within one thread, so `plan` (draft, then design) holds it across both steps.
`implement` is the exception: it holds the lock shared, plus a lock on its own node, so
several nodes can be implemented at once (`implement --jobs N`, or several terminals)
while any other writer is refused until they are all done. It is an
advisory `flock` on a file in the git directory (or `hiraku/` outside git), released by
the operating system if the process dies, so a crash never leaves a stale lock.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import functools
import inspect
import json
import os
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, TypeVar

from hiraku.config import HirakuConfig

from .errors import AgentError
from .launches import launch_dir

try:
    import fcntl
except ImportError:  # pragma: no cover - not POSIX: no advisory locks to take
    fcntl = None  # type: ignore[assignment]

# (path, owning thread) -> (file descriptor, depth). Re-entry is only for the thread
# that took the lock; any other thread contends for it like a separate process would
# (`flock` locks belong to an open file, and each thread opens its own).
_held: dict[tuple[Path, int], tuple[int, int]] = {}
_guard = threading.Lock()

F = TypeVar("F", bound=Callable[..., Any])

IMPLEMENTING_DIRNAME = "implementing"


def lock_path(config: HirakuConfig) -> Path:
    return launch_dir(config).parent / "hiraku-agent.lock"


def _node_lock_path(config: HirakuConfig, target: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in target) or "_"
    return lock_path(config).parent / IMPLEMENTING_DIRNAME / f"{safe}.lock"


def _describe(info: dict[str, Any]) -> str:
    call = " ".join(
        part
        for part in ("hiraku-agent", info.get("workflow"), info.get("target"))
        if part
    )
    return f"`{call}` (pid {info.get('pid', '?')}, started {info.get('started', '?')})"


def _read_info(path: Path) -> dict[str, Any]:
    try:
        info = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return {}
    return info if isinstance(info, dict) else {}


def running_implements(config: HirakuConfig) -> list[dict[str, Any]]:
    """The `implement` calls holding a node lock right now (any process)."""
    directory = lock_path(config).parent / IMPLEMENTING_DIRNAME
    found: list[dict[str, Any]] = []
    if fcntl is None or not directory.is_dir():
        return found
    for path in sorted(directory.glob("*.lock")):
        try:
            descriptor = os.open(path, os.O_RDONLY)
        except OSError:
            continue
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            found.append(_read_info(path) or {"target": path.stem})
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
    return found


def _holder(config: HirakuConfig, path: Path) -> str:
    info = _read_info(path)
    if info:
        return _describe(info)
    running = running_implements(config)
    if running:
        names = ", ".join(str(item.get("target", "?")) for item in running)
        return f"{len(running)} `implement` call(s), on {names}"
    return "another hiraku-agent call"


def _info(workflow: str, target: str) -> bytes:
    return json.dumps(
        {
            "pid": os.getpid(),
            "workflow": workflow,
            "target": target,
            "started": _dt.datetime.now().isoformat(timespec="seconds"),
        }
    ).encode()


@contextlib.contextmanager
def _flock(
    path: Path, *, shared: bool, info: bytes | None, refused: Callable[[], str]
) -> Iterator[None]:
    """Hold `flock` on `path` (re-entrant per thread), or raise naming the holder."""
    key = (path, threading.get_ident())
    with _guard:
        reentered = key in _held
        if reentered:
            descriptor, depth = _held[key]
            _held[key] = (descriptor, depth + 1)
    if not reentered:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        try:
            fcntl.flock(descriptor, mode | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            raise AgentError(refused()) from None
        if info is not None:
            os.ftruncate(descriptor, 0)
            os.write(descriptor, info)
        with _guard:
            _held[key] = (descriptor, 1)
    try:
        yield
    finally:
        with _guard:
            descriptor, depth = _held[key]
            if depth > 1:
                _held[key] = (descriptor, depth - 1)
            else:
                del _held[key]
                if info is not None:
                    with contextlib.suppress(OSError):
                        os.ftruncate(descriptor, 0)
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)


@contextlib.contextmanager
def project_lock(
    config: HirakuConfig, workflow: str, target: str, *, per_target: bool = False
) -> Iterator[None]:
    """Hold the project's writer lock, or raise `AgentError` naming who has it.

    `per_target` (`implement`) holds it shared, plus an exclusive lock on `target`:
    implements of different nodes run side by side (each writes only its own node, in
    its own worktree), while one of the same node, or any other writer, is refused.
    """
    if fcntl is None:
        yield
        return
    path = lock_path(config)
    if not per_target:
        with _flock(
            path,
            shared=False,
            info=_info(workflow, target),
            refused=lambda: (
                f"Another hiraku-agent call is writing to this project: "
                f"{_holder(config, path)}. Wait for it to finish, then run this again."
            ),
        ):
            yield
        return
    with _flock(
        path,
        shared=True,
        info=None,
        refused=lambda: (
            f"Another hiraku-agent call is writing to this project: "
            f"{_holder(config, path)}. Wait for it to finish, then run this again."
        ),
    ):
        node_path = _node_lock_path(config, target)
        with _flock(
            node_path,
            shared=False,
            info=_info(workflow, target),
            refused=lambda: (
                f"'{target}' is already being implemented: "
                f"{_describe(_read_info(node_path))}."
            ),
        ):
            yield


@contextlib.contextmanager
def git_lock(config: HirakuConfig) -> Iterator[None]:
    """Serialize short repository changes (branches, worktrees) between parallel
    `implement` calls; waits instead of failing, since each holds it for seconds."""
    if fcntl is None:
        yield
        return
    path = lock_path(config).parent / "hiraku-agent-git.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def writes(workflow: str, *, per_target: bool = False) -> Callable[[F], F]:
    """Run the decorated `run_*` under `project_lock`, unless it will write nothing.

    The call's first positional argument names its target (a task, node or record id,
    or the instruction for `draft`/`plan`, which have no target yet). `dry_run=True` or
    `apply=False` skip the lock. `per_target`: see `project_lock`.
    """

    def decorate(function: F) -> F:
        signature = inspect.signature(function)

        @functools.wraps(function)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            bound = signature.bind_partial(*args, **kwargs)
            arguments = bound.arguments
            if arguments.get("dry_run") or arguments.get("apply") is False:
                return function(*args, **kwargs)
            config = arguments.get("config") or HirakuConfig.discover()
            first = next(iter(arguments.values()), "")
            target = " ".join(str(first or "").split())[:60]
            with project_lock(config, workflow, target, per_target=per_target):
                return function(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorate
