"""One writer at a time per project.

Every workflow re-reads a record before writing it, but that is not atomic: a `loop`
step and a manual call (or two terminals) can each pass their checks and then both
write — two `triage`s deciding the same node, or a `design` adding children to a node a
`revise` is rewriting. Each writing workflow therefore holds this lock from its checks
to its last write, agent call included. A second writer fails at once and says who
holds the lock, rather than waiting minutes behind an agent call or racing it.

`--dry-run` and `--no-apply` write nothing and take no lock. The lock is re-entrant
within one process, so `plan` (draft, then design) holds it across both steps. It is an
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

# path -> (file descriptor, depth, owning thread). Re-entry is only for the thread that
# took the lock; any other caller contends for it like a separate process would.
_held: dict[Path, tuple[int, int, int]] = {}
_guard = threading.Lock()

F = TypeVar("F", bound=Callable[..., Any])


def lock_path(config: HirakuConfig) -> Path:
    return launch_dir(config).parent / "hiraku-agent.lock"


def _holder(path: Path) -> str:
    try:
        info = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        info = {}
    if not info:
        return "another hiraku-agent call"
    call = " ".join(
        part
        for part in ("hiraku-agent", info.get("workflow"), info.get("target"))
        if part
    )
    return f"`{call}` (pid {info.get('pid', '?')}, started {info.get('started', '?')})"


@contextlib.contextmanager
def project_lock(config: HirakuConfig, workflow: str, target: str) -> Iterator[None]:
    """Hold the project's writer lock, or raise `AgentError` naming who has it."""
    if fcntl is None:
        yield
        return
    path = lock_path(config)
    me = threading.get_ident()
    with _guard:
        reentered = path in _held and _held[path][2] == me
        if reentered:
            descriptor, depth, _ = _held[path]
            _held[path] = (descriptor, depth + 1, me)
    if not reentered:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            raise AgentError(
                f"Another hiraku-agent call is writing to this project: "
                f"{_holder(path)}. Wait for it to finish, then run this again."
            ) from None
        os.ftruncate(descriptor, 0)
        os.write(
            descriptor,
            json.dumps(
                {
                    "pid": os.getpid(),
                    "workflow": workflow,
                    "target": target,
                    "started": _dt.datetime.now().isoformat(timespec="seconds"),
                }
            ).encode(),
        )
        with _guard:
            _held[path] = (descriptor, 1, me)
    try:
        yield
    finally:
        with _guard:
            descriptor, depth, _ = _held[path]
            if depth > 1:
                _held[path] = (descriptor, depth - 1, me)
            else:
                del _held[path]
                with contextlib.suppress(OSError):
                    os.ftruncate(descriptor, 0)
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)


def writes(workflow: str) -> Callable[[F], F]:
    """Run the decorated `run_*` under `project_lock`, unless it will write nothing.

    The call's first positional argument names its target (a task, node or record id,
    or the instruction for `draft`/`plan`, which have no target yet). `dry_run=True` or
    `apply=False` skip the lock.
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
            with project_lock(config, workflow, target):
                return function(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorate
