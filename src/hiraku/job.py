"""Research attribution and foreground output for native PSI/J jobs."""

from __future__ import annotations

import builtins
import codecs
import json
import logging
import os
import sys
import time
from collections.abc import Mapping, Sequence
from contextlib import ExitStack, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

import psij
import psutil
from psij.serialize import JSONSerializer
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from .errors import ExecutionError, ObservationError, ValidationError
from .io import atomic_write

if TYPE_CHECKING:
    from .project import Project

logger = logging.getLogger(__name__)


def validate_command(value: Any) -> list[str] | None:
    """Validate command configuration as a literal argv sequence."""
    if value is None:
        return None
    if isinstance(value, str | bytes) or not isinstance(value, list | tuple):
        raise ValidationError("command must be an argv sequence")
    command = list(value)
    if not command or not isinstance(command[0], str) or not command[0]:
        raise ValidationError("command executable must be a non-empty string")
    if any(not isinstance(arg, str) or "\x00" in arg for arg in command):
        raise ValidationError("command arguments must be strings without NUL bytes")
    return command


def prepare_job(
    command: Sequence[str],
    *,
    project: Project,
    record_id: str | None = None,
    env: Mapping[str, str] | None = None,
    attributes: psij.JobAttributes | None = None,
    resources: psij.ResourceSpec | None = None,
    directory: Path | None = None,
    launcher: str | None = None,
) -> psij.Job:
    """Construct a native PSI/J job with explicit research attribution.

    Does not submit, write project history, or resolve the active record.
    Paths and resource requests describe the execution environment.
    """
    argv = validate_command(command)
    if argv is None:
        raise ValidationError("command must be an argv sequence")
    if record_id is not None:
        project.vault.load(record_id)
    additions = dict(env or {})
    if any(
        not isinstance(k, str)
        or not k
        or "=" in k
        or "\x00" in k
        or not isinstance(v, str)
        or "\x00" in v
        for k, v in additions.items()
    ):
        raise ValidationError("Environment additions must map strings to strings")
    if {"HIRAKU_RECORD_ID", "HIRAKU_JOB_ID"} & additions.keys():
        raise ValidationError("Environment cannot override reserved run attribution")
    job = psij.Job()
    job.spec = psij.JobSpec(
        executable=argv[0],
        arguments=argv[1:],
        directory=directory,
        attributes=attributes,
        resources=resources,
        launcher=launcher,
        environment={
            **additions,
            "HIRAKU_RECORD_ID": record_id or "",
            "HIRAKU_JOB_ID": job.id,
        },
    )
    return job


def run_local(job: psij.Job, *, project: Project | None = None) -> psij.JobStatus:
    """Submit locally, stream output, and return the native final status.

    Output uses temporary files unless the caller supplied output paths.
    Supplying a project records the submission and its final native status.
    """
    if job.status.state != psij.JobState.NEW or job.executor is not None:
        raise psij.InvalidJobException("Job must be new and unbound")
    spec = job.spec
    if spec is None:
        raise ValidationError("A job specification is required")
    original = spec.stdout_path, spec.stderr_path
    submission = None
    with TemporaryDirectory(prefix="hiraku-output-") as temporary:
        spec.stdout_path = spec.stdout_path or Path(temporary) / "stdout"
        spec.stderr_path = spec.stderr_path or Path(temporary) / "stderr"
        spec.stdin_path = spec.stdin_path or Path(os.devnull)
        spec.launcher = spec.launcher or "quoted-single"
        try:
            psij.JobExecutor.get_instance("local").submit(job)
            if project is not None:
                submission = Submission.from_psi_job(job)
                submission.save(project)
            _follow_streams(job)
        except BaseException as exc:
            if job.native_id is not None and not job.status.final:
                job.cancel()
                # PSI/J publishes cancellation before its reaper collects the child.
                with suppress(psutil.NoSuchProcess):
                    process = psutil.Process(int(job.native_id))
                    deadline = time.monotonic() + 10
                    while process.is_running():
                        if time.monotonic() >= deadline:
                            raise ExecutionError(
                                "Local cancellation timed out"
                            ) from exc
                        time.sleep(0.05)
            raise
        finally:
            spec.stdout_path, spec.stderr_path = original
            if project is not None and submission is not None and job.status.final:
                submission.sync(job)
                submission.save(project)
    return job.status


def _submission_path(job_id: str, project: Project) -> Path:
    try:
        Submission.validate_id(job_id)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ExecutionError(f"Invalid job ID: {job_id!r}") from exc
    return project.root / ".hiraku" / "jobs" / f"{job_id}.json"


class Status(BaseModel):
    """Validated status data for observations and saved submission outcomes."""

    model_config = ConfigDict(strict=True, extra="forbid")

    state: Literal["NEW", "QUEUED", "ACTIVE", "COMPLETED", "FAILED", "CANCELED"]
    time: float | None
    exit_code: int | None
    message: str | None
    metadata: dict[str, Any] | None

    @classmethod
    def from_psi_status(cls, status: psij.JobStatus) -> Status:
        """Copy a native PSI/J status into validated, serializable data."""
        return cls(
            state=status.state.name,
            time=status.time,
            exit_code=status.exit_code,
            message=status.message,
            metadata=status.metadata,
        )


class Submission(BaseModel):
    """Validated submission metadata persisted independently of execution."""

    model_config = ConfigDict(strict=True, extra="forbid", validate_assignment=True)

    id: str
    record_id: str | None
    submitted_at: AwareDatetime
    observed_at: AwareDatetime
    executor: str = Field(min_length=1)
    executor_url: str | None
    native_id: str = Field(min_length=1)
    spec: dict[str, Any]
    status: Status

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if str(UUID(value)) != value:
            raise ValueError("Expected a canonical PSI/J UUID")
        return value

    @field_validator("spec")
    @classmethod
    def validate_spec(cls, value: dict[str, Any]) -> dict[str, Any]:
        arguments = value.get("arguments", [])
        if not isinstance(arguments, list):
            raise ValueError("Command arguments must be a list")
        try:
            validate_command([value.get("executable"), *arguments])
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc
        return value

    @classmethod
    def from_psi_job(cls, job: psij.Job) -> Submission:
        """Copy accepted job metadata without executing or writing anything."""
        if job.executor is None or job.native_id is None or job.spec is None:
            raise ExecutionError("Only submitted jobs can be recorded")
        try:
            status = Status.from_psi_status(job.status)
            now = datetime.now(UTC)
            return cls(
                id=job.id,
                record_id=(job.spec.environment or {}).get("HIRAKU_RECORD_ID") or None,
                submitted_at=now,
                observed_at=now,
                executor=job.executor.name,
                executor_url=job.executor.url,
                native_id=job.native_id,
                spec=json.loads(JSONSerializer().dumps(job.spec)),
                status=status,
            )
        except (TypeError, ValueError) as exc:
            raise ExecutionError(
                f"Job {job.id} was submitted (native ID {job.native_id}), "
                f"but its history could not be serialized: {exc}"
            ) from exc

    def get_psi_job(self) -> psij.Job:
        """Attach a native job without waiting, syncing, or saving metadata."""
        if self.executor == "local":
            raise ObservationError("Native local jobs cannot be safely reattached")
        try:
            executor = psij.JobExecutor.get_instance(
                self.executor, url=self.executor_url
            )
            job = psij.Job()
            executor.attach(job, self.native_id)
            return job
        except (
            psij.InvalidJobException,
            psij.SubmitException,
            OSError,
            ValueError,
            TypeError,
            KeyError,
            ImportError,
            NotImplementedError,
        ) as exc:
            raise ObservationError(f"Cannot attach job {self.id}: {exc}") from exc

    def sync(self, job: psij.Job | None = None) -> None:
        """Attach if needed and copy current status without waiting or saving."""
        if job is None:
            job = self.get_psi_job()
        if job.executor is None or (
            job.executor.name,
            job.executor.url,
            job.native_id,
        ) != (self.executor, self.executor_url, self.native_id):
            raise ValidationError(
                "Job executor target and native ID must match submission"
            )
        try:
            status = Status.from_psi_status(job.status)
            observed_at = datetime.now(UTC)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ObservationError(
                f"Cannot read status for job {self.id}: {exc}"
            ) from exc
        self.status = status
        self.observed_at = observed_at

    def save(self, project: Project) -> None:
        """Atomically save submission metadata."""
        try:
            atomic_write(
                _submission_path(self.id, project),
                type(self).model_validate(self.model_dump()).model_dump_json(indent=2),
                mode=0o600,
            )
        except (OSError, TypeError, ValueError) as exc:
            raise ExecutionError(
                f"Cannot save history for job {self.id} "
                f"({self.executor}, native ID {self.native_id}): {exc}. "
                "The job was submitted; do not resubmit automatically."
            ) from exc

    @classmethod
    def load(cls, project: Project, job_id: str) -> Submission:
        """Read validated history without contacting an executor."""
        path = _submission_path(job_id, project)
        try:
            entry = cls.model_validate_json(path.read_text(encoding="utf-8"))
            if entry.id != job_id:
                raise ValueError("History ID differs from filename")
            return entry
        except (OSError, ValueError) as exc:
            raise ExecutionError(
                f"Cannot read history for job {job_id}: {exc}"
            ) from exc

    @classmethod
    def list(
        cls, project: Project, *, record_id: str | None = None
    ) -> builtins.list[Submission]:
        """Read history newest first, warning and continuing past corrupt entries."""
        entries = []
        for path in (project.root / ".hiraku" / "jobs").glob("*.json"):
            try:
                entry = cls.load(project, path.stem)
            except ExecutionError as exc:
                logger.warning("%s", exc)
                continue
            if record_id is None or entry.record_id == record_id:
                entries.append(entry)
        return sorted(
            entries, key=lambda entry: (entry.submitted_at, entry.id), reverse=True
        )


def _follow_streams(job: psij.Job) -> None:
    directory = job.spec.directory or Path.cwd()
    with ExitStack() as stack:
        streams = [
            [path, output, None, codecs.getincrementaldecoder("utf-8")("replace")]
            for path, output in (
                (directory / job.spec.stdout_path, sys.stdout),
                (directory / job.spec.stderr_path, sys.stderr),
            )
        ]
        while True:
            done = job.wait(timeout=timedelta(seconds=0.1)) is not None
            for stream in streams:
                path, output, file, decoder = stream
                if file is None and path.is_file():
                    file = stream[2] = stack.enter_context(path.open("rb"))
                if file is not None:
                    data = file.read()
                    buffer = getattr(output, "buffer", None)
                    if buffer is not None:
                        buffer.write(data)
                        buffer.flush()
                    else:
                        output.write(decoder.decode(data, final=done))
                        output.flush()
            if done:
                break
