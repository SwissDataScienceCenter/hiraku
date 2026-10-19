"""POSIX local process executor with a detached supervisor."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..errors import BackendSubmissionError, JobError
from ..io import atomic_write
from ..job import Job, JobSpec, _read
from .base import ComputeBackend

_HEARTBEAT_TIMEOUT = 15


def _write_state(directory: Path, **values: Any) -> None:
    state_path = directory / "local-state.json"
    state = {}
    if state_path.exists():
        state = json.loads(state_path.read_text())
    state.update(values)
    atomic_write(state_path, json.dumps(state, indent=2), mode=0o600)


def _read_state(directory: Path) -> dict[str, object]:
    try:
        value = json.loads((directory / "local-state.json").read_text())
    except (OSError, ValueError):
        return {"status": "unknown"}
    return value if isinstance(value, dict) else {"status": "unknown"}


class LocalBackend(ComputeBackend):
    """Execute a prepared source snapshot in a detached local process group."""

    def __init__(self, jobs_dir: Path) -> None:
        self.jobs_dir = jobs_dir.resolve()

    def submit(self, spec: JobSpec) -> dict[str, object]:
        if os.name != "posix":
            raise JobError("Local detached jobs currently require a POSIX system")
        job_dir = self.jobs_dir / spec.job_id
        job = _read(job_dir)
        if job.source_path is None:
            raise JobError("Local jobs require a prepared source snapshot")
        source_path = job.source_path
        working_directory = source_path / spec.project_path
        environment = os.environ.copy()
        for name in (
            "HIRAKU_JOB_ID",
            "HIRAKU_JOB_DIR",
            "HIRAKU_RECORD_ID",
            "HIRAKU_RECORD_FILE",
            "HIRAKU_ROOT",
            "HIRAKU_TRACKING_ROOT",
            "HIRAKU_COMMIT",
            "HIRAKU_GIT_STATE",
            "HIRAKU_SOURCE_DIGEST",
            "HIRAKU_ENTRYPOINT",
        ):
            environment.pop(name, None)
        environment.update(spec.env)
        bootstrap = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from hiraku.compute.local import _supervise; _supervise(sys.argv[2])"
        )
        directory = job_dir
        try:
            with (directory / "supervisor.log").open("ab") as output:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        bootstrap,
                        str(Path(__file__).resolve().parents[2]),
                        str(directory),
                    ],
                    cwd=working_directory,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=output,
                    start_new_session=True,
                )
        except OSError as exc:
            raise BackendSubmissionError(
                f"Could not start job supervisor: {exc}"
            ) from exc
        handle: dict[str, object] = {
            "directory": str(directory),
            "supervisor_pid": process.pid,
        }
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            state = _read_state(directory)
            if state.get("supervisor_pid"):
                return handle
            if process.poll() is not None:
                raise JobError(
                    "Job supervisor exited during startup; see supervisor.log"
                )
            time.sleep(0.02)
        raise BackendSubmissionError(
            "Job supervisor startup timed out; inspect job status and supervisor.log",
            uncertain=True,
        )

    def status(self, handle: dict[str, object]) -> dict[str, object]:
        directory_value = handle.get("directory")
        if directory_value is None:
            return {"status": "unknown"}
        directory = Path(str(directory_value))
        state = _read_state(directory)
        status = state.get("status", "unknown")
        for key in ("started_at", "finished_at"):
            value = state.get(key)
            if isinstance(value, str):
                state[key] = datetime.fromisoformat(value)
        if status in {"succeeded", "failed", "cancelled"}:
            return state
        try:
            age = time.time() - (directory / "heartbeat").stat().st_mtime
        except OSError:
            return {"status": "unknown"}
        pid = handle.get("supervisor_pid") or state.get("supervisor_pid")
        if pid is None:
            return {"status": "unknown"}
        try:
            supervisor_pid = int(pid)
        except (TypeError, ValueError):
            return {"status": "unknown"}
        supervisor_start = state.get("supervisor_start")
        supervisor_matches = True
        if sys.platform.startswith("linux"):
            supervisor_matches = (
                isinstance(supervisor_start, str)
                and _process_start(supervisor_pid) == supervisor_start
            )
        if not supervisor_matches:
            if sys.platform.startswith("linux"):
                child_pid = state.get("child_pid")
                child_start = state.get("child_start")
                child_known = isinstance(child_pid, int) and isinstance(
                    child_start, str
                )
                child_alive = child_known and _process_start(child_pid) == child_start
                if not child_known:
                    return {"status": "unknown"}
                return {
                    **state,
                    "status": "orphaned" if child_alive else "lost",
                }
            return {"status": "unknown"}
        if age > _HEARTBEAT_TIMEOUT:
            return {"status": "unknown"}
        try:
            os.kill(supervisor_pid, 0)
        except (ProcessLookupError, PermissionError, ValueError):
            return {"status": "unknown"}
        return state

    def cancel(self, handle: dict[str, object]) -> None:
        directory_value = handle.get("directory")
        if directory_value is None:
            return
        directory = Path(str(directory_value))
        if self.status(handle).get("status") not in {
            "succeeded",
            "failed",
            "cancelled",
            "lost",
        }:
            (directory / "cancel").touch(mode=0o600, exist_ok=True)
            self._cancel_orphaned_group(directory)

    @staticmethod
    def _cancel_orphaned_group(directory: Path) -> None:
        """Stop a child group if Linux confirms its supervisor has disappeared."""
        if not sys.platform.startswith("linux"):
            return
        state = _read_state(directory)
        supervisor_pid = state.get("supervisor_pid")
        supervisor_start = state.get("supervisor_start")
        if (
            isinstance(supervisor_pid, int)
            and isinstance(supervisor_start, str)
            and _process_start(supervisor_pid) == supervisor_start
        ):
            return
        child_pid = state.get("child_pid")
        child_start = state.get("child_start")
        if (
            not isinstance(child_pid, int)
            or not isinstance(child_start, str)
            or _process_start(child_pid) != child_start
        ):
            return
        with suppress(ProcessLookupError):
            os.killpg(child_pid, signal.SIGTERM)
        time.sleep(1)
        if _process_start(child_pid) == child_start and _group_has_members(child_pid):
            with suppress(ProcessLookupError):
                os.killpg(child_pid, signal.SIGKILL)

    def logs(
        self, handle: dict[str, object], *, stream: str, follow: bool
    ) -> Iterator[str]:
        directory = Path(str(handle["directory"]))
        with (directory / f"{stream}.log").open(
            encoding="utf-8", errors="replace"
        ) as output:
            while True:
                text = output.read()
                if text:
                    yield text
                if not follow or self.status(handle).get("status") in {
                    "succeeded",
                    "failed",
                    "cancelled",
                    "lost",
                    "orphaned",
                }:
                    tail = output.read()
                    if tail:
                        yield tail
                    return
                time.sleep(0.1)


def _stop(process: subprocess.Popen[Any]) -> None:
    # Keep the group leader unreaped through escalation, preventing PID reuse.
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    time.sleep(1)
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def _supervise(directory_name: str) -> None:
    directory = Path(directory_name)
    process = None
    try:
        (directory / "heartbeat").touch()
        job = _read(directory)
        _write_state(
            directory,
            status="submitted",
            supervisor_pid=os.getpid(),
            supervisor_start=_process_start(os.getpid()),
            started_at=None,
            finished_at=None,
            exit_code=None,
            error=None,
        )
        with (
            (directory / "stdout.log").open("ab", buffering=0) as stdout,
            (directory / "stderr.log").open("ab", buffering=0) as stderr,
        ):
            if (directory / "cancel").exists():
                _write_state(
                    directory,
                    status="cancelled",
                    finished_at=datetime.now(UTC).isoformat(),
                )
                return
            command = list(job.command)
            if job.dispatcher:
                command = [
                    str(job.root / job.dispatcher),
                    "--",
                    *command,
                ]
            process = subprocess.Popen(
                command,
                cwd=job.working_directory,
                env={**os.environ, **job.spec.env, **_attribution(job)},
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
            _write_state(
                directory,
                child_pid=process.pid,
                child_start=_process_start(process.pid),
            )
            _write_state(
                directory, status="running", started_at=datetime.now(UTC).isoformat()
            )
            while True:
                (directory / "heartbeat").touch()
                code = process.poll()
                if code is not None:
                    _write_state(
                        directory,
                        status="succeeded" if code == 0 else "failed",
                        exit_code=code,
                        finished_at=datetime.now(UTC).isoformat(),
                    )
                    return
                if (directory / "cancel").exists():
                    _stop(process)
                    _write_state(
                        directory,
                        status="cancelled",
                        exit_code=process.returncode,
                        finished_at=datetime.now(UTC).isoformat(),
                    )
                    return
                time.sleep(1)
    except BaseException as exc:
        if process is not None and process.returncode is None:
            _stop(process)
        _write_state(
            directory,
            status="failed",
            error=str(exc),
            finished_at=datetime.now(UTC).isoformat(),
        )


def _attribution(job: Job) -> dict[str, str]:
    return {
        "HIRAKU_JOB_ID": job.id,
        "HIRAKU_JOB_DIR": str(job.directory),
        "HIRAKU_RECORD_ID": job.record_id,
        "HIRAKU_RECORD_FILE": str(job.record_file),
        "HIRAKU_ROOT": str(job.root),
        "HIRAKU_TRACKING_ROOT": str(job.tracking_root),
        "HIRAKU_COMMIT": job.commit,
        "HIRAKU_GIT_STATE": "dirty" if job.spec.source_dirty else "clean",
        "HIRAKU_SOURCE_DIGEST": job.spec.source_digest,
    }


def _process_start(pid: int) -> str | None:
    """Return a Linux process start token, excluding zombies and reused PIDs."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except (OSError, IndexError):
        return None
    if not fields or fields[0] == "Z":
        return None
    return fields[19] if len(fields) > 19 else None


def _group_has_members(group_id: int) -> bool:
    """Check whether a Linux process group still exists before escalation."""
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            fields = (process / "stat").read_text().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        if len(fields) > 2 and fields[2] == str(group_id) and fields[0] != "Z":
            return True
    return False
