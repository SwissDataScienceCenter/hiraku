"""Research attribution and foreground output for native PSI/J jobs."""

from __future__ import annotations

import builtins
import codecs
import json
import os
import sys
import time
import warnings
from collections.abc import Mapping, Sequence
from contextlib import ExitStack, nullcontext, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

import psij
import psutil
from filelock import FileLock
from psij.executors.batch.slurm import SlurmExecutorConfig
from psij.serialize import JSONSerializer
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from .errors import ExecutionError, ObservationError, ValidationError
from .io import atomic_write

if TYPE_CHECKING:
    from .project import Project


class JobRequest(BaseModel):
    """Typed parameters for submitting a project job."""

    model_config = ConfigDict(extra="forbid", strict=True)

    command: list[str] = Field(
        min_length=1,
        description="Command as an argv list; each item is one literal argument.",
        examples=["uv", "run", "my_script.py", "--option", "value"],
    )
    # TODO: find another way of specifying the list of executors.
    executor: Literal["local", "slurm", "local-detached", "renku"] = Field(
        default="local-detached",
        description="Registered PSI/J executor name.",
    )
    record_id: str | None = Field(
        default=None,
        description="Record ID to attribute this job to; omit for no attribution.",
    )
    executor_url: str | None = Field(
        default=None,
        description="Optional URL for the selected executor.",
    )
    custom_attributes: dict[str, str] | None = Field(
        default=None,
        description="Optional executor-specific string attributes.",
    )
    directory: str | None = Field(
        default=None,
        description="Optional working directory for the command.",
    )
    launcher: str | None = Field(
        default=None,
        description="Optional registered PSI/J launcher name.",
    )

    @field_validator("command")
    @classmethod
    def validate_argv(cls, value: list[str]) -> list[str]:
        try:
            command = validate_command(value)
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc
        if command is None:
            raise ValueError("command must be an argv sequence")
        return command


def get_executor(
    name: str, *, work_directory: Path, url: str | None = None
) -> psij.JobExecutor:
    """Create an executor with an explicit auxiliary storage directory."""
    config = (
        SlurmExecutorConfig(work_directory=work_directory, keep_files=True)
        if name.lower() == "slurm"
        else psij.JobExecutorConfig(work_directory=work_directory)
    )
    return psij.JobExecutor.get_instance(name, url=url, config=config)


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

    Does not submit or write project history.
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


def submit_job(
    job: psij.Job, *, project: Project, executor: str, url: str | None = None
) -> Submission:
    """Submit a prepared job in the background and save its history."""
    backend = get_executor(executor, work_directory=project.psij_dir, url=url)
    if executor.lower() == "slurm" and job.spec is not None:
        work = (backend.config.work_directory / backend.name).absolute()
        job.spec.stdout_path = job.spec.stdout_path or work / f"{job.id}.stdout"
        job.spec.stderr_path = job.spec.stderr_path or work / f"{job.id}.stderr"
        job.spec.launcher = job.spec.launcher or "quoted-single"
    backend.submit(job)
    entry = Submission.from_psi_job(job)
    entry.save(project)
    return entry


def run_local(job: psij.Job, *, project: Project) -> psij.JobStatus:
    """Submit locally, stream output, and return the native final status.

    Output uses temporary files unless the caller supplied output paths.
    Records the submission and its final native status in the project.
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
            get_executor("local", work_directory=project.psij_dir).submit(job)
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
            if submission is not None and job.status.final:
                submission.sync(job)
                submission.save(project)
    return job.status


class Status(BaseModel):
    """Validated status data for observations and saved submission outcomes."""

    model_config = ConfigDict(strict=True, extra="forbid")

    state: Literal["NEW", "QUEUED", "ACTIVE", "COMPLETED", "FAILED", "CANCELED"]
    time: float | None
    exit_code: int | None
    message: str | None
    metadata: dict[str, Any] | None

    @property
    def final(self) -> bool:
        return psij.JobState[self.state].final

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
    executor_work_directory: str = Field(min_length=1)
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
                executor_work_directory=str(
                    job.executor.config.work_directory.absolute()
                ),
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
            executor = get_executor(
                self.executor,
                url=self.executor_url,
                work_directory=Path(self.executor_work_directory),
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
        """Copy native status, allowing a fresh Slurm attachment time to poll."""
        attached_here = job is None
        if job is None:
            if self.status.final:
                return
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
            native_status = job.status
            if (
                attached_here
                and self.executor.lower() == "slurm"
                and native_status.state == psij.JobState.NEW
            ):
                job.wait(
                    timeout=timedelta(seconds=8), target_states=psij.JobState.QUEUED
                )
                native_status = job.status
            if native_status.state == psij.JobState.NEW:
                return
            status = Status.from_psi_status(native_status)
            observed_at = datetime.now(UTC)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ObservationError(
                f"Cannot read status for job {self.id}: {exc}"
            ) from exc
        self.status = status
        self.observed_at = observed_at

    def refresh(self, project: Project) -> None:
        """Update and save the snapshot of a submission that is still in progress."""
        if not self.status.final:
            self.sync()
            self.save(project)

    def save(self, project: Project) -> None:
        """Atomically save submission metadata."""
        try:
            entry = type(self).model_validate(self.model_dump())
            path = project.jobs_dir / f"{entry.id}.json"
            is_slurm = entry.executor.lower() == "slurm"
            lock = (
                FileLock(path.with_suffix(".lock"), timeout=10)
                if is_slurm
                else nullcontext()
            )
            with lock:
                if is_slurm:
                    # A concurrent observer may have saved a newer or final result.
                    with suppress(FileNotFoundError):
                        saved = type(self).model_validate_json(
                            path.read_text(encoding="utf-8")
                        )
                        if saved.id == entry.id and (
                            saved.status.final
                            or (
                                not entry.status.final
                                and saved.observed_at >= entry.observed_at
                            )
                        ):
                            self.status = saved.status
                            self.observed_at = saved.observed_at
                            return
                atomic_write(path, entry.model_dump_json(indent=2), mode=0o600)
        except (OSError, TypeError, ValueError) as exc:
            raise ExecutionError(
                f"Cannot save history for job {self.id} "
                f"({self.executor}, native ID {self.native_id}): {exc}. "
                "The job was submitted; do not resubmit automatically."
            ) from exc

    def logs(self, project: Project) -> dict[str, str | None]:
        """Read available stdout and stderr files without observing the job."""
        spec = self.spec
        directory = Path(spec.get("directory") or project.root)
        streams: dict[str, str | None] = {}
        for name in ("stdout", "stderr"):
            value = spec.get(f"{name}_path")
            if value is None:
                streams[name] = None
                continue
            if not isinstance(value, str):
                raise ExecutionError(
                    f"Cannot read {name} logs for job {self.id}: invalid path"
                )
            path = Path(value)
            if not path.is_absolute():
                path = directory / path
            try:
                streams[name] = (
                    path.read_text(encoding="utf-8", errors="replace")
                    if path.is_file()
                    else None
                )
            except OSError as exc:
                raise ExecutionError(
                    f"Cannot read {name} logs for job {self.id}: {exc}"
                ) from exc
        return streams

    def cancel(self, project: Project) -> None:
        """Cancel the native job and persist its resulting status."""
        if self.status.final:
            return
        job = self.get_psi_job()
        try:
            job.cancel()
        except (
            psij.InvalidJobException,
            psij.SubmitException,
            OSError,
            ValueError,
            TypeError,
        ) as exc:
            raise ExecutionError(f"Cannot cancel job {self.id}: {exc}") from exc
        self.sync(job)
        self.save(project)

    @classmethod
    def load(cls, project: Project, job_id: str) -> Submission:
        """Read validated history without contacting an executor."""
        try:
            cls.validate_id(job_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ExecutionError(f"Invalid job ID: {job_id!r}") from exc
        path = project.jobs_dir / f"{job_id}.json"
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
    def from_prefix(cls, project: Project, prefix: str) -> Submission:
        """Load the unique submission matching a UUID or nonempty prefix."""
        if (
            not isinstance(prefix, str)
            or not 1 <= len(prefix) <= 36
            or any(char not in "0123456789abcdef-" for char in prefix)
        ):
            raise ValidationError(
                "Job ID must be a UUID or a nonempty hexadecimal UUID prefix"
            )
        if len(prefix) == 36:
            return cls.load(project, prefix)
        matches = []
        for path in project.jobs_dir.glob(f"{prefix}*.json"):
            try:
                cls.validate_id(path.stem)
            except ValueError:
                continue
            matches.append(path.stem)
        if not matches:
            raise ExecutionError(f"No submission matches job ID prefix {prefix!r}")
        if len(matches) > 1:
            raise ValidationError(
                f"Ambiguous job ID prefix {prefix!r}; provide more characters"
            )
        return cls.load(project, matches[0])

    @classmethod
    def list(
        cls, project: Project, *, record_id: str | None = None
    ) -> builtins.list[Submission]:
        """Read history newest first, warning and continuing past corrupt entries."""
        entries = []
        for path in project.jobs_dir.glob("*.json"):
            try:
                entry = cls.load(project, path.stem)
            except ExecutionError as exc:
                warnings.warn(str(exc), RuntimeWarning, stacklevel=2)
                continue
            if record_id is None or entry.record_id == record_id:
                entries.append(entry)
        return sorted(
            entries, key=lambda entry: (entry.submitted_at, entry.id), reverse=True
        )


def observe_submission(entry: Submission, project: Project) -> dict[str, Any]:
    """Return current submission data, saving observations of nonfinal jobs.

    Observation failures return UNKNOWN and an error without changing history.
    Callers own warning reporting; storage failures propagate.
    """
    try:
        entry.refresh(project)
    except ObservationError as exc:
        result = entry.model_dump(mode="json")
        result["status"]["state"] = "UNKNOWN"
        result["observation_error"] = str(exc)
        return result
    return entry.model_dump(mode="json")


def wait_for_submissions(
    entries: Sequence[Submission],
    *,
    project: Project,
    any_final: bool = False,
    timeout: timedelta | None = None,
) -> bool:
    """Block until the submissions are final, saving each final status.

    Args:
        entries: Submissions to wait for; they are updated in place.
        project: Project whose history receives the observed statuses.
        any_final: Return as soon as one submission is final instead of all.
        timeout: Return after this long even if the condition is not met.

    Returns:
        Whether the condition was met before the timeout. Submissions that are
        still running are synced and saved like ``hiraku job status``.
    """
    if timeout is not None and timeout < timedelta(0):
        raise ValidationError("Timeout must not be negative")
    if any_final and any(entry.status.final for entry in entries):
        return True
    # Already-final submissions are never reattached: PSI/J may have consumed the
    # auxiliary files that hold their exit codes.
    pending = [
        (entry, entry.get_psi_job()) for entry in entries if not entry.status.final
    ]
    deadline = None if timeout is None else time.monotonic() + timeout.total_seconds()

    def record(items: Sequence[tuple[Submission, psij.Job]]) -> None:
        for entry, job in items:
            entry.sync(job)
            entry.save(project)

    while pending:
        for item in list(pending):
            remaining = None if deadline is None else deadline - time.monotonic()
            # timedelta rounds anything below a microsecond to zero, which
            # PSI/J's wait() reads as "no timeout"; treat the last millisecond
            # as expired so the deadline always holds.
            if remaining is not None and remaining < 0.001:
                record(pending)
                return False
            # Short slices let several jobs be watched in turn; PSI/J's own
            # status polling runs in the background either way.
            step = 1.0 if remaining is None else min(1.0, remaining)
            if item[1].wait(timeout=timedelta(seconds=step)) is None:
                continue
            record([item])
            pending.remove(item)
            if any_final:
                record(pending)
                return True
    return True


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
