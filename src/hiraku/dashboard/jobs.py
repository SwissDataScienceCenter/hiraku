"""Background launch controller for the interactive dashboard.

The controller owns dashboard launch jobs at server-process scope. Requests are
validated, written to a temporary directory outside the project, and executed
by ``hiraku.dashboard.worker``. The notebook only polls process state; it never
blocks on a running launch and never invokes entrypoint commands directly.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from ..errors import EntrypointNotFoundError, RecordNotFoundError, ValidationError
from ..git import GitRepository
from ..launch import _dispatcher, render_arguments
from .actions import preflight_mutations
from .capability import DashboardCapability
from .data import DashboardError, _resolve_all

MAX_COMPLETED_JOBS = 20
TAIL_BYTES = 64 * 1024


def parse_override_yaml(raw: str) -> dict[str, Any]:
    """Parse launch argument overrides without executing a command."""
    text = raw.strip()
    if not text:
        return {}
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValidationError(f"Invalid overrides YAML: {exc}") from exc
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValidationError("Overrides must be a YAML mapping")
    overrides: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValidationError("Override keys must be strings")
        overrides[key] = item
    render_arguments(overrides)
    return overrides


@dataclass(frozen=True)
class LaunchRequest:
    """Validated, serializable launch request owned by the controller."""

    job_id: str
    record_id: str
    entrypoint: str
    args: Mapping[str, Any]
    dispatch: str | None = None
    commit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "project_root": "",  # filled by the controller
            "record_id": self.record_id,
            "entrypoint": self.entrypoint,
            "args": dict(self.args),
            "dispatch": self.dispatch,
            "commit": self.commit,
        }


@dataclass(frozen=True)
class JobView:
    """Immutable, JSON-friendly job status view for the UI."""

    job_id: str
    record_id: str
    entrypoint: str
    submitted_at: datetime
    state: str
    exit_code: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class JobLogs:
    """Bounded stdout/stderr tails for one launch job."""

    stdout_tail: str
    stderr_tail: str


@dataclass(frozen=True)
class LaunchPreview:
    """Nonmutating launch preview returned by the controller."""

    record_id: str
    entrypoint: str
    argv: tuple[str, ...]
    dispatcher: str
    commit: str | None
    commit_source: str
    query_source: str | None


@dataclass(frozen=True)
class _Preflight:
    merged_args: dict[str, Any]
    commit: str | None
    values: dict[str, Any]
    sources: dict[str, Any]


@dataclass
class _Job:
    request: LaunchRequest
    process: subprocess.Popen[Any]
    directory: Path
    stdout_path: Path
    stderr_path: Path
    submitted_at: datetime
    state: str = "starting"
    exit_code: int | None = None
    error: str | None = None
    completion_emitted: bool = False

    def view(self) -> JobView:
        return JobView(
            job_id=self.request.job_id,
            record_id=self.request.record_id,
            entrypoint=self.request.entrypoint,
            submitted_at=self.submitted_at,
            state=self.state,
            exit_code=self.exit_code,
            error=self.error,
        )


def _tail(path: Path, limit: int = TAIL_BYTES) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    offset = max(0, size - limit)
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            return handle.read()
    except OSError:
        return ""


def _concise_error(stderr_tail: str) -> str:
    lines = [line.strip() for line in stderr_tail.splitlines() if line.strip()]
    if not lines:
        return "Launch failed without stderr output"
    return lines[-1][:500]


class LaunchController:
    """Own launch jobs and poll their worker processes without blocking."""

    def __init__(
        self,
        config: Any,
        capability: DashboardCapability,
        *,
        python: str | None = None,
        temp_dir: Path | None = None,
    ) -> None:
        self._config = config
        self._capability = capability
        self._python = python or sys.executable
        self._lock = threading.RLock()
        self._accepting = True
        self._jobs: dict[str, _Job] = {}
        self._completed_order: list[str] = []
        if temp_dir is not None:
            self._temp_dir = temp_dir
            self._temp_dir.mkdir(parents=True, exist_ok=True)
            self._owns_temp_dir = False
        else:
            self._temp_dir = Path(tempfile.mkdtemp(prefix="hiraku-dashboard-jobs-"))
            self._owns_temp_dir = True

    @property
    def accepting(self) -> bool:
        with self._lock:
            return self._accepting

    @property
    def active_job_id(self) -> str | None:
        with self._lock:
            for job in self._jobs.values():
                if job.state in {"starting", "running"}:
                    return job.request.job_id
        return None

    def _preflight(self, request: LaunchRequest) -> _Preflight:
        self._capability.ensure_can_launch()
        index = preflight_mutations(self._config)
        if request.record_id not in index:
            raise RecordNotFoundError(f"Record {request.record_id!r} not found")
        errors: list[DashboardError] = []
        resolved = _resolve_all(index, errors)
        if errors or request.record_id not in resolved:
            messages = [error.message for error in errors]
            raise ValidationError(
                "Project records are not safe to launch: " + " ".join(messages)
            )
        values, sources = resolved[request.record_id]
        entrypoints = values.get("entrypoints") or {}
        if request.entrypoint not in entrypoints:
            raise EntrypointNotFoundError(
                f"Record {request.record_id!r} has no entrypoint {request.entrypoint!r}"
            )
        entrypoint = entrypoints[request.entrypoint]
        entrypoint_args = (
            dict(entrypoint.get("args") or {})
            if isinstance(entrypoint, Mapping)
            else dict(entrypoint.args)
        )
        merged_args = dict(entrypoint_args)
        merged_args.update(request.args)
        render_arguments(request.args)
        render_arguments(merged_args)
        _dispatcher(self._config, request.dispatch)
        commit = request.commit
        if commit is not None and commit:
            commit = GitRepository(self._config).resolve(commit)
        return _Preflight(
            merged_args=merged_args,
            commit=commit,
            values=values,
            sources=sources,
        )

    def preview(self, request: LaunchRequest) -> LaunchPreview:
        """Validate and render a launch without starting a worker."""
        preflight = self._preflight(request)
        entrypoint = (preflight.values.get("entrypoints") or {})[request.entrypoint]
        command = (
            entrypoint.get("command")
            if isinstance(entrypoint, Mapping)
            else list(entrypoint.command)
        )
        dispatcher_path = _dispatcher(self._config, request.dispatch)
        query_source = None
        try:
            from .data import _backend_for_values, _source_info

            backend = _backend_for_values(self._config, preflight.values)
            query_source = _source_info(
                backend, self._config, request.record_id, "record"
            ).label
        except Exception:
            query_source = None
        return LaunchPreview(
            record_id=request.record_id,
            entrypoint=request.entrypoint,
            argv=tuple(command) + render_arguments(preflight.merged_args),
            dispatcher=dispatcher_path.name,
            commit=preflight.commit,
            commit_source="override" if request.commit else "record",
            query_source=query_source,
        )

    def submit(self, request: LaunchRequest) -> JobView:
        """Validate and start one launch worker."""
        with self._lock:
            if not self._accepting:
                raise ValidationError("Launch controller is shutting down")
            if request.job_id in self._jobs:
                raise ValidationError(f"Duplicate launch event {request.job_id!r}")
            if self.active_job_id is not None:
                raise ValidationError(
                    "Another launch is already active; wait for it to finish"
                )
            preflight = self._preflight(request)
            resolved_commit = preflight.commit
            request = LaunchRequest(
                job_id=request.job_id,
                record_id=request.record_id,
                entrypoint=request.entrypoint,
                args=request.args,
                dispatch=request.dispatch,
                commit=resolved_commit,
            )
            job_dir = self._temp_dir / request.job_id
            job_dir.mkdir()
            request_path = job_dir / "request.json"
            payload = request.to_dict()
            payload["project_root"] = str(self._config.root)
            request_path.write_text(json.dumps(payload), encoding="utf-8")
            stdout_path = job_dir / "stdout.log"
            stderr_path = job_dir / "stderr.log"
            stdout_handle = stdout_path.open("wb")
            stderr_handle = stderr_path.open("wb")
            try:
                command = [
                    self._python,
                    "-m",
                    "hiraku.dashboard.worker",
                    str(request_path),
                ]
                kwargs: dict[str, Any] = {}
                if os.name == "posix":
                    kwargs["start_new_session"] = True
                process = subprocess.Popen(
                    command,
                    cwd=self._config.root,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    env=os.environ.copy(),
                    **kwargs,
                )
            except Exception:
                stdout_handle.close()
                stderr_handle.close()
                shutil.rmtree(job_dir, ignore_errors=True)
                raise
            finally:
                stdout_handle.close()
                stderr_handle.close()
            job = _Job(
                request=request,
                process=process,
                directory=job_dir,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                submitted_at=datetime.now(UTC),
                state="running",
            )
            self._jobs[request.job_id] = job
            return job.view()

    def poll(self) -> list[JobView]:
        """Reap terminal workers and return all current job views."""
        with self._lock:
            for job in list(self._jobs.values()):
                if job.state == "starting":
                    job.state = "running"
                if job.state != "running":
                    continue
                returncode = job.process.poll()
                if returncode is None:
                    continue
                job.state = "succeeded" if returncode == 0 else "failed"
                job.exit_code = returncode
                if returncode != 0:
                    job.error = _concise_error(_tail(job.stderr_path))
                if not job.completion_emitted:
                    job.completion_emitted = True
                    self._completed_order.append(job.request.job_id)
            self._trim_completed()
            return [job.view() for job in self._sorted_jobs()]

    def logs(self, job_id: str) -> JobLogs:
        """Return bounded log tails for a known job."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ValidationError(f"Unknown launch job {job_id!r}")
            stdout_path = job.stdout_path
            stderr_path = job.stderr_path
        return JobLogs(
            stdout_tail=_tail(stdout_path),
            stderr_tail=_tail(stderr_path),
        )

    def _sorted_jobs(self) -> list[_Job]:
        return sorted(
            self._jobs.values(), key=lambda job: job.submitted_at, reverse=True
        )

    def _trim_completed(self) -> None:
        while len(self._completed_order) > MAX_COMPLETED_JOBS:
            job_id = self._completed_order.pop(0)
            job = self._jobs.pop(job_id, None)
            if job is not None:
                shutil.rmtree(job.directory, ignore_errors=True)

    def _terminate_tree(self, process: subprocess.Popen[Any]) -> None:
        if process.poll() is not None:
            return
        if os.name == "posix":
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                process.terminate()
        else:
            process.terminate()
        with suppress(subprocess.TimeoutExpired):
            if process.wait(timeout=5) is not None:
                return
        if os.name == "posix":
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                process.kill()
        else:
            process.kill()
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)

    def shutdown(self, *, timeout: float = 30.0) -> None:
        """Stop accepting launches and drain or terminate owned workers."""
        with self._lock:
            self._accepting = False
            jobs = list(self._jobs.values())
        deadline = time.monotonic() + timeout
        for job in jobs:
            if job.process.poll() is not None:
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                job.process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                break
        self.poll()
        for job in jobs:
            if job.process.poll() is None:
                self._terminate_tree(job.process)
        for job in jobs:
            if job.process.poll() is None:
                with suppress(subprocess.TimeoutExpired):
                    job.process.wait(timeout=5)
        self.poll()
        if self._owns_temp_dir:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
