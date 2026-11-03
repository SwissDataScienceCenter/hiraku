"""Persistent Hiraku jobs backed by PSI/J.

- Record/Job/TrackingBackend are distinct concepts.
- PSI/J owns submission and scheduler status; Hiraku persists job attribution.
- Executed projects receive only HIRAKU_JOB_ID from Hiraku.
- Tracking integration is deliberately outside this module.
- Optional project dispatchers configure the command, not the scheduler.
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import psutil
from psij import Job as PsiJob
from psij import JobAttributes, JobExecutor, JobSpec, JobState, ResourceSpec
from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from .errors import (
    ExecutionError,
    GitError,
    HirakuError,
    NoActiveRecordError,
    ValidationError,
)
from .io import atomic_write

if TYPE_CHECKING:
    from .config import HirakuConfig
    from .run import Run
    from .tracking.base import TrackingBackend

JobStatus = Literal[
    "created",
    "submitting",
    "submitted",
    "queued",
    "running",
    "canceling",
    "completed",
    "failed",
    "canceled",
    "unknown",
]
TERMINAL = frozenset({"completed", "failed", "canceled"})


class Job(BaseModel):
    """Durable research-execution identity (not a PSI/J Job or tracked Run)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[0-9a-f]{32}$")
    record_id: str
    command: list[str]  # User-facing, unwrapped argv.
    directory: str
    commit: str | None = None
    git_state: Literal["clean", "dirty", "unavailable"] = "unavailable"
    executor: str = "local"
    dispatch: str | None = None
    state: JobStatus = "created"
    exit_code: int | None = None
    native_id: str | None = None
    native_process_created: float | None = None  # Local PID-reuse guard.
    stdout: str | None = None
    stderr: str | None = None
    cancel_requested: bool = False
    error: str | None = None  # Submission/inspection diagnostics, not run logs.
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def tracked_runs(self, backend: TrackingBackend) -> list[Run]:
        """Return tracked runs attributed to this job."""
        return [
            run
            for run in backend.list_runs(record_id=self.record_id)
            if run.job_id == self.id
        ]

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        atomic_write(directory / f"{self.id}.json", self.model_dump_json(indent=2))

    @classmethod
    def load(cls, directory: Path, job_id: str) -> Job:
        if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
            raise ExecutionError(f"Invalid job ID: {job_id!r}")
        try:
            content = (directory / f"{job_id}.json").read_text(encoding="utf-8")
            job = cls.model_validate_json(content)
        except (OSError, PydanticValidationError, ValueError) as exc:
            raise ExecutionError(f"Cannot load job {job_id!r}: {exc}") from exc
        if job.id != job_id:
            raise ExecutionError(f"Job metadata ID mismatch: {job_id!r}")
        return job


def job_directory(config: HirakuConfig) -> Path:
    return config.hiraku_dir / "jobs"


def current_job(config: HirakuConfig | None = None) -> Job | None:
    """Return the persisted job associated with this process, if available."""
    job_id = os.environ.get("HIRAKU_JOB_ID")
    if not job_id:
        return None
    try:
        from .config import HirakuConfig as Config

        selected = config
        if selected is None:
            root = os.environ.get("HIRAKU_ROOT")
            selected = Config.discover(Path(root) if root else None)
        return Job.load(job_directory(selected), job_id)
    except (HirakuError, OSError, ValueError):
        return None


def _environment(additions: Mapping[str, str] | None, job_id: str) -> dict[str, str]:
    environment = dict(additions or {})
    if any(
        not isinstance(key, str)
        or not key
        or "=" in key
        or "\x00" in key
        or not isinstance(value, str)
        or "\x00" in value
        for key, value in environment.items()
    ):
        raise ValidationError("Environment additions must map valid strings to strings")
    if "HIRAKU_JOB_ID" in environment:
        raise ValidationError("Cannot override HIRAKU_JOB_ID")
    # Do not inject HIRAKU_RECORD_ID, HIRAKU_ROOT, MLflow, or W&B settings here.
    environment["HIRAKU_JOB_ID"] = job_id
    return environment


def _resolve_dispatch(
    argv: list[str], dispatch: str | None, config: HirakuConfig
) -> tuple[list[str], str | None]:
    name = dispatch if dispatch is not None else config.default_dispatch
    if name is None:
        return argv, None
    if (
        not isinstance(name, str)
        or not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or "\x00" in name
    ):
        raise ValidationError(f"Invalid dispatcher name: {name!r}")
    filename = name if Path(name).suffix else f"{name}.sh"
    script = config.dispatch_dir / filename
    if not script.is_file():
        if dispatch is not None:
            raise ExecutionError(f"Dispatcher does not exist: {script}")
        return argv, None  # Backwards-compatible absent default dispatcher.
    return [str(script), "--", *argv], name


def _source_revision(root: Path) -> tuple[str | None, str]:
    from .git import GitRepository

    try:
        repository = GitRepository(root)
        return repository.resolve("HEAD"), ("dirty" if repository.is_dirty else "clean")
    except GitError:
        return None, "unavailable"


def _prepare(
    command: Sequence[str] | None,
    *,
    record_id: str | None,
    config: HirakuConfig,
    executor: str,
    dispatch: str | None,
) -> tuple[Job, list[str]]:
    from .execution import validate_command
    from .record import load_record

    selected = record_id or config.active_record_id
    if selected is None:
        raise NoActiveRecordError("Specify --record or select an active record")
    record = load_record(selected, config=config)

    if command is None:
        # Commands are record-local and never inherited from project settings.
        command = (record.model_extra or {}).get("command")
    if command is None or isinstance(command, (str, bytes)):
        raise ExecutionError(
            "Specify a command after -- or define record 'command' argv"
        )
    argv = validate_command(list(command))
    if argv is None:
        raise ExecutionError("Command is required")

    actual, used_dispatch = _resolve_dispatch(argv, dispatch, config)
    commit, git_state = _source_revision(config.root)
    job = Job(
        record_id=record.id,
        command=argv,
        directory=str(config.root),
        commit=commit,
        git_state=git_state,
        executor=executor,
        dispatch=used_dispatch,
    )
    folder = job_directory(config)
    job.stdout = str(folder / f"{job.id}.out")
    job.stderr = str(folder / f"{job.id}.err")
    return job, actual


class ComputeBackend:
    """Thin PSI/J adapter for local or scheduler-backed execution.

    `directory`, stdout and stderr paths must exist on the target compute system.
    Stage-in/out and SSH code deployment are intentionally not implemented.
    """

    def __init__(self, executor_name: str = "local") -> None:
        self.executor_name = executor_name
        self._native_jobs: dict[str, PsiJob] = {}

    def _executor(self) -> JobExecutor:
        return JobExecutor.get_instance(self.executor_name)

    def submit(
        self,
        job: Job,
        argv: list[str],
        environment: Mapping[str, str],
        *,
        resources: ResourceSpec | None = None,
        attributes: JobAttributes | None = None,
    ) -> Job:
        """Submit prepared job; the caller owns persistence and preparation."""
        if job.executor != self.executor_name or not argv or job.state != "created":
            raise ExecutionError("Invalid prepared job for submission")
        executable, arguments = argv[0], argv[1:]
        spec = JobSpec(
            executable=executable,
            arguments=arguments,
            directory=job.directory,
            environment=dict(environment),
            inherit_environment=True,
            stdout_path=job.stdout,
            stderr_path=job.stderr,
            resources=resources,
            attributes=attributes,
        )
        psi_job = PsiJob(spec)
        folder = Path(job.stdout).parent if job.stdout else Path(job.directory)
        job.state = "submitting"
        job.save(folder)
        try:
            self._executor().submit(psi_job)
        except Exception as exc:
            job.state = "failed"
            job.error = f"PSI/J submission failed: {exc}"
            job.save(folder)
            raise ExecutionError(job.error) from exc

        if psi_job.native_id is None:
            job.state = "failed"
            job.error = "PSI/J did not return a native job ID"
            job.save(folder)
            raise ExecutionError(job.error)
        job.native_id = str(psi_job.native_id)
        self._native_jobs[job.id] = psi_job
        if self.executor_name == "local" and job.native_id:
            with contextlib.suppress(ValueError, psutil.Error, OSError):
                job.native_process_created = psutil.Process(
                    int(job.native_id)
                ).create_time()
        job.state = "submitted"
        try:
            job.save(folder)
        except OSError as exc:
            raise ExecutionError(
                f"Submitted job {job.id} (native_id={job.native_id!r}), "
                f"but saving the submission metadata failed: {exc}"
            ) from exc
        return job

    def _attach(self, job: Job) -> tuple[JobExecutor, PsiJob]:
        if not job.native_id:
            raise ExecutionError(f"Job {job.id} has no native execution ID")
        executor = self._executor()
        native = PsiJob()
        executor.attach(native, job.native_id)
        return executor, native

    def refresh(self, folder: Path, job_id: str) -> Job:
        job = Job.load(folder, job_id)
        if job.executor != self.executor_name:
            raise ExecutionError(f"Job {job_id} belongs to executor {job.executor!r}")
        if job.state in TERMINAL:
            return job

        if not job.native_id:
            return job  # Persisted before submit; not safe to infer failure.
        try:
            native = self._native_jobs.get(job.id)
            if native is None:
                _, native = self._attach(job)
            status = native.wait(
                timeout=timedelta(seconds=2), target_states=[JobState.QUEUED]
            )
        except Exception as exc:
            # Inspection failures must not silently become execution failures.
            job.error = f"Cannot inspect job status: {exc}"
            job.save(folder)
            return job

        if status is None or status.state == JobState.NEW:
            return job

        mapping = {
            JobState.QUEUED: "queued",
            JobState.ACTIVE: "running",
            JobState.COMPLETED: "completed",
            JobState.FAILED: "failed",
            JobState.CANCELED: "canceled",
        }
        state = mapping.get(status.state, "unknown")
        job.state = (
            "canceling" if job.cancel_requested and state not in TERMINAL else state
        )
        job.exit_code = status.exit_code
        job.error = None
        job.save(folder)
        return job

    def cancel(self, folder: Path, job_id: str) -> Job:
        job = self.refresh(folder, job_id)
        if job.state in TERMINAL:
            raise ExecutionError(f"Job {job_id} is already {job.state}")
        if not job.native_id:
            raise ExecutionError(f"Job {job_id} has no native execution ID")
        if job.executor == "local":
            # PSI/J's local native ID is a PID; ensure it was not reused.
            if job.native_process_created is None:
                raise ExecutionError(
                    "Cannot safely cancel local job without PID identity"
                )
            try:
                process = psutil.Process(int(job.native_id))
                if abs(process.create_time() - job.native_process_created) > 0.01:
                    raise ExecutionError("Cannot cancel: native PID has been reused")
                for child in process.children(recursive=True):
                    with contextlib.suppress(psutil.NoSuchProcess):
                        child.kill()
                process.kill()
                process.wait(timeout=5)
            except (ValueError, psutil.Error, OSError) as exc:
                raise ExecutionError(
                    f"Cannot cancel local job {job_id}: {exc}"
                ) from exc
            job.state = "canceled"
        else:
            executor, native = self._attach(job)
            try:
                executor.cancel(native)
            except Exception as exc:
                raise ExecutionError(
                    f"Cannot request cancellation of {job_id}: {exc}"
                ) from exc
            # Cancellation is asynchronous; wait for the scheduler to confirm.
            job.state = "canceling"
            job.cancel_requested = True
        job.save(folder)
        return job

    def logs(self, job: Job) -> tuple[Path | None, Path | None]:
        return (
            Path(job.stdout) if job.stdout else None,
            Path(job.stderr) if job.stderr else None,
        )

    def wait(
        self,
        folder: Path,
        job_id: str,
        *,
        timeout: float | None = None,
        poll_interval: float = 1.0,
    ) -> Job:
        """Poll until terminal; raise on timeout, never invent a terminal state."""
        if poll_interval <= 0 or (timeout is not None and timeout < 0):
            raise ValueError("timeout must be non-negative and poll_interval positive")
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            job = self.refresh(folder, job_id)
            if job.state in TERMINAL:
                return job
            if deadline is not None and time.monotonic() >= deadline:
                raise ExecutionError(
                    f"Timed out waiting for job {job_id} ({job.state})"
                )
            time.sleep(poll_interval)


class LocalBackend(ComputeBackend):
    def __init__(self) -> None:
        super().__init__("local")


def submit(
    command: Sequence[str] | None = None,
    *,
    record_id: str | None = None,
    executor: str = "local",
    config: HirakuConfig,
    dispatch: str | None = None,
    env: Mapping[str, str] | None = None,
    resources: ResourceSpec | None = None,
    attributes: JobAttributes | None = None,
    _backend: ComputeBackend | None = None,
) -> Job:
    """High-level entry point for the CLI, MCP, and workflows.

    The tracking layer may later use HIRAKU_JOB_ID to resolve the persisted Job,
    derive record/commit attribution, and tag a tracked Run with job_id.
    This module must never import or instantiate a TrackingBackend.
    """
    prepared, argv = _prepare(
        command,
        record_id=record_id,
        config=config,
        executor=executor,
        dispatch=dispatch,
    )
    environment = _environment(env, prepared.id)
    prepared.save(job_directory(config))
    backend = _backend or (
        LocalBackend() if executor == "local" else ComputeBackend(executor)
    )
    if backend.executor_name != executor:
        raise ExecutionError(
            f"Backend {backend.executor_name!r} does not match executor {executor!r}"
        )
    return backend.submit(
        prepared, argv, environment, resources=resources, attributes=attributes
    )


def refresh(config: HirakuConfig, job_id: str) -> Job:
    folder = job_directory(config)
    job = Job.load(folder, job_id)
    backend = (
        LocalBackend() if job.executor == "local" else ComputeBackend(job.executor)
    )
    return backend.refresh(folder, job_id)


def cancel(config: HirakuConfig, job_id: str) -> Job:
    folder = job_directory(config)
    job = Job.load(folder, job_id)
    backend = (
        LocalBackend() if job.executor == "local" else ComputeBackend(job.executor)
    )
    return backend.cancel(folder, job_id)
