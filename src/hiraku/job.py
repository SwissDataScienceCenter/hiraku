"""Durable execution identities and public job services."""

from __future__ import annotations

import json
import shutil
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator
from pydantic import ValidationError as ModelValidationError

from .config import HirakuConfig
from .errors import (
    BackendSubmissionError,
    JobError,
    NoActiveRecordError,
    ValidationError,
)
from .git import GitRepository
from .record import load_record
from .source import prepare_source, require_clean
from .validation import validate_command

JobStatus = Literal[
    "submitted",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "lost",
    "orphaned",
    "unknown",
]
TERMINAL = frozenset({"succeeded", "failed", "cancelled", "lost"})


class JobSpec(BaseModel):
    """Immutable, resolved request passed to an execution backend."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    job_id: str
    command: tuple[str, ...]
    record_id: str
    record_snapshot: Mapping[str, Any]
    project_path: str
    revision: str | None
    source_digest: str | None
    source_dirty: bool | None
    source_mode: Literal["snapshot", "existing"] = "snapshot"
    source_location: str | None = None
    compute_config: Mapping[str, Any] = Field(
        default_factory=lambda: MappingProxyType({})
    )
    target: str = "local"
    env: Mapping[str, str] = Field(default_factory=lambda: MappingProxyType({}))
    dispatcher: str | None = None

    @field_validator("command", mode="before")
    @classmethod
    def _tuple_command(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, (tuple, list)) else value

    @field_validator("env", "compute_config", mode="before")
    @classmethod
    def _copy_env(cls, value: Any) -> Any:
        return _freeze(value) if isinstance(value, Mapping) else value

    @field_validator("record_snapshot", mode="before")
    @classmethod
    def _freeze_snapshot(cls, value: Any) -> Any:
        return _freeze(value)

    @field_serializer("env", "record_snapshot")
    def _serialize_frozen_mapping(self, value: Any) -> dict[str, Any]:
        return _thaw(value)


class Job(BaseModel):
    """Persisted execution provenance, independent of experimental tracking runs."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[2] = 2
    id: str
    spec: JobSpec
    directory: Path
    source_path: Path | None = None
    project_root: Path
    backend: str = "local"
    backend_handle: dict[str, Any] = Field(default_factory=dict)
    status: JobStatus = "submitted"
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    exit_code: int | None = None
    error: str | None = None

    @property
    def record_id(self) -> str:
        return self.spec.record_id

    @property
    def command(self) -> tuple[str, ...]:
        return self.spec.command

    @property
    def record_snapshot(self) -> Mapping[str, Any]:
        return self.spec.record_snapshot

    @property
    def commit(self) -> str:
        return self.spec.revision or ""

    @property
    def root(self) -> Path:
        if self.source_path is not None:
            return self.source_path / self.spec.project_path
        return Path(self.spec.source_location or ".")

    @property
    def tracking_root(self) -> Path:
        return self.project_root

    @property
    def working_directory(self) -> Path:
        return self.root

    @property
    def record_file(self) -> Path:
        return self.directory / "record.md"

    @property
    def dispatcher(self) -> str | None:
        return self.spec.dispatcher


def _directory(job_id: str, config: HirakuConfig) -> Path:
    # UUID hex IDs cannot escape the store, including through traversal or symlinks.
    if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
        raise ValidationError(f"Invalid job ID: {job_id!r}")
    directory = config.jobs_dir / job_id
    if directory.is_symlink():
        raise JobError(f"Job directory must not be a symbolic link: {directory}")
    return directory


def _read(directory: Path) -> Job:
    try:
        job = Job.model_validate_json((directory / "job.json").read_text())
        if job.directory != directory.resolve() or job.id != directory.name:
            raise ValueError("job manifest identity does not match its directory")
        return job
    except (OSError, ValueError, ModelValidationError) as exc:
        raise JobError(f"Cannot read job at {directory}: {exc}") from exc


def load_job(job_id: str, *, config: HirakuConfig | None = None) -> Job:
    """Read a job's saved manifest without refreshing backend status."""
    selected = config or HirakuConfig.discover()
    return _read(_directory(job_id, selected).resolve())


def _backend(name: str = "local", *, config: HirakuConfig | None = None) -> Any:
    selected = config or HirakuConfig.discover()
    if name == "local":
        from .compute import LocalBackend

        return LocalBackend(selected.jobs_dir)
    if name == "slurm":
        from .compute import SlurmBackend

        return SlurmBackend(selected.jobs_dir)
    raise JobError(f"Unsupported compute backend: {name!r}")


def _save_fields(directory: Path, **values: Any) -> Job:
    """Serialize lifecycle writes shared by the store and local supervisor."""
    import fcntl

    with (directory / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        job = _read(directory)
        if job.status not in TERMINAL:
            job = job.model_copy(update=values)
            _write(job)
        return job


def submit_job(
    command: Sequence[str] | None = None,
    *,
    record_id: str | None = None,
    backend: str = "local",
    commit: str | None = None,
    dispatch: str | None = None,
    env: Mapping[str, str] | None = None,
    dry_run: bool = False,
    config: HirakuConfig | None = None,
) -> Job | dict[str, Any]:
    """Resolve a request, persist its receipt, then submit it to a backend."""
    selected = config or HirakuConfig.discover()
    if backend not in {"local", "slurm"}:
        raise ValidationError(f"Unsupported compute backend: {backend!r}")
    backend_name = backend
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
    }
    if reserved.intersection(additions):
        raise ValidationError("Environment cannot override reserved job attribution")
    job_id = uuid.uuid4().hex
    directory = (selected.jobs_dir / job_id).resolve()
    workspace = directory / "workspace"
    dispatcher = None
    revision: str | None = None
    source_digest: str | None = None
    source_dirty: bool | None = None
    source_mode: Literal["snapshot", "existing"] = "snapshot"
    source_location: str | None = None
    project_path = ""
    compute_config: dict[str, Any] = {}
    repository: GitRepository | None = None
    pinned_revision = commit or record.commit

    repository = GitRepository(selected)
    require_clean(repository)
    revision = repository.resolve(pinned_revision or "HEAD")
    project_path = selected.root.resolve().relative_to(repository.root).as_posix()
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
            raise JobError(f"Dispatcher does not exist: {dispatcher_path}")
        if dispatcher_path.is_file():
            dispatcher = dispatcher_path.relative_to(selected.root).as_posix()

    if dry_run:
        return {
            "job_id": job_id,
            "record_id": record.id,
            "command": argv,
            "backend": backend_name,
            "revision": revision,
            "source_mode": source_mode,
            "source_location": source_location,
            "dispatcher": dispatcher,
        }
    store = selected.jobs_dir.resolve()
    if selected.jobs_dir.is_symlink():
        raise JobError("Job store must not be a symbolic link")
    store.mkdir(parents=True, exist_ok=True, mode=0o700)
    (store / ".gitignore").write_text("*\n", encoding="utf-8")
    directory.mkdir(mode=0o700)
    dispatch_started = False
    try:
        source = None
        if repository is not None:
            source = prepare_source(repository, workspace, pinned_revision)
            (workspace / project_path).mkdir(parents=True, exist_ok=True)
            if dispatcher and not (workspace / project_path / dispatcher).is_file():
                raise JobError(
                    f"Dispatcher {dispatcher!r} is absent from resolved source "
                    f"{revision}; commit it or select another source revision"
                )
            revision = source.base_revision
            source_digest = source.digest
            source_dirty = source.dirty
        spec = JobSpec(
            job_id=job_id,
            command=tuple(argv),
            record_id=record.id,
            record_snapshot=_snapshot(record),
            project_path=project_path,
            revision=revision,
            source_digest=source_digest,
            source_dirty=source_dirty,
            source_mode=source_mode,
            source_location=source_location,
            compute_config=compute_config,
            target=backend_name,
            env=additions,
            dispatcher=dispatcher,
        )
        job = Job(
            id=job_id,
            spec=spec,
            directory=directory,
            source_path=workspace if source is not None else None,
            project_root=selected.root.resolve(),
            backend=backend_name,
        )
        _write(job)
        job.record_file.write_text(
            record.path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        for stream in ("stdout", "stderr"):
            (directory / f"{stream}.log").touch(mode=0o600)
        handle = {"directory": str(directory)}
        _save_fields(directory, backend_handle=handle)
        dispatch_started = True
        handle = _backend(backend_name, config=selected).submit(spec)
        return _save_fields(directory, backend_handle=handle)
    except Exception as exc:
        if directory.exists() and not (directory / "job.json").exists():
            shutil.rmtree(directory, ignore_errors=True)
        if directory.exists():
            try:
                job = _read(directory)
                uncertain = dispatch_started and not (
                    isinstance(exc, BackendSubmissionError) and not exc.uncertain
                )
                _save_fields(
                    directory,
                    status="unknown" if uncertain else "failed",
                    error=str(exc),
                    finished_at=None if uncertain else datetime.now(UTC),
                )
            except JobError:
                pass
        raise JobError(f"Could not submit job {job_id}: {exc}") from exc


def job_status(job_id: str, *, config: HirakuConfig | None = None) -> Job:
    """Refresh lifecycle state, reconciling supervisors that disappeared."""
    job = load_job(job_id, config=config)
    if job.status in TERMINAL:
        return job
    selected = config or HirakuConfig.discover()
    observed = _backend(job.backend, config=selected).status(job.backend_handle)
    status = observed.get("status", "unknown")
    if status == "unknown":
        return job.model_copy(update={"status": "unknown"})
    if status not in {"submitted", "running", "orphaned", *TERMINAL}:
        return job.model_copy(update={"status": "unknown"})
    values = {
        key: observed[key]
        for key in ("status", "started_at", "finished_at", "exit_code", "error")
        if key in observed and getattr(job, key) != observed[key]
    }
    if values:
        job = _save_fields(job.directory, **values)
    return job


def list_jobs(
    *, record_id: str | None = None, config: HirakuConfig | None = None
) -> list[Job]:
    """List persisted jobs, optionally filtered by record ID."""
    selected = config or HirakuConfig.discover()
    jobs = [
        job_status(path.parent.name, config=selected)
        for path in sorted(selected.jobs_dir.glob("*/job.json"))
    ]
    return sorted(
        (job for job in jobs if record_id is None or job.record_id == record_id),
        key=lambda job: (job.submitted_at, job.id),
    )


def wait_job(
    job_id: str,
    *,
    timeout: float | None = None,
    unknown_timeout: float = 30.0,
    config: HirakuConfig | None = None,
) -> Job:
    """Wait for a terminal job state; a timeout does not cancel execution."""
    if timeout is not None and timeout < 0:
        raise ValidationError("timeout must be non-negative")
    if unknown_timeout < 0:
        raise ValidationError("unknown_timeout must be non-negative")
    deadline = None if timeout is None else time.monotonic() + timeout
    unknown_since: float | None = None
    while True:
        job = job_status(job_id, config=config)
        if job.status in TERMINAL:
            return job
        if job.status == "orphaned":
            raise JobError(
                f"Job {job_id} is still running but its supervisor is unavailable; "
                "inspect logs or cancel it"
            )
        if job.status == "unknown":
            if unknown_since is None:
                unknown_since = time.monotonic()
            if time.monotonic() - unknown_since >= unknown_timeout:
                raise JobError(
                    f"Could not observe job {job_id} for {unknown_timeout:g} seconds"
                )
        else:
            unknown_since = None
        if deadline is not None and time.monotonic() >= deadline:
            raise JobError(f"Timed out waiting for job {job_id}")
        time.sleep(1 if job.status == "unknown" else 0.1)


def cancel_job(job_id: str, *, config: HirakuConfig | None = None) -> Job:
    """Request idempotent cancellation; the supervisor signals its own child."""
    job = job_status(job_id, config=config)
    if job.status in TERMINAL:
        return job
    selected = config or HirakuConfig.discover()
    _backend(job.backend, config=selected).cancel(job.backend_handle)
    return job_status(job_id, config=config)


def job_logs(
    job_id: str,
    *,
    stream: Literal["stdout", "stderr"] = "stdout",
    follow: bool = False,
    config: HirakuConfig | None = None,
) -> Iterator[str]:
    """Yield saved output, optionally following it until execution terminates."""
    if stream not in {"stdout", "stderr"}:
        raise ValidationError("stream must be stdout or stderr")
    job = load_job(job_id, config=config)
    handle = {"directory": str(job.directory), **job.backend_handle}
    selected = config or HirakuConfig.discover()
    yield from _backend(job.backend, config=selected).logs(
        handle, stream=stream, follow=follow
    )


def _write(job: Job) -> None:
    from .io import atomic_write

    atomic_write(job.directory / "job.json", job.model_dump_json(indent=2), mode=0o600)


def _snapshot(record: Any) -> dict[str, Any]:
    # Record serialization already validates YAML-compatible values.
    return json.loads(record.model_dump_json(exclude={"path"}))


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value
