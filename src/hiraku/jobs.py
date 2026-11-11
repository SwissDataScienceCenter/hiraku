"""Local PSI/J jobs with atomic JSON snapshots and saved output."""

from __future__ import annotations

import codecs
import json
import logging
import os
import re
import socket
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import suppress
from copy import copy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

import psij
from filelock import FileLock, Timeout
from psij.serialize import JSONSerializer
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    field_serializer,
    field_validator,
)

from .errors import (
    ExecutionError,
    GitError,
    JobNotFoundError,
    NoActiveRecordError,
    ValidationError,
)
from .git import GitRepository
from .io import atomic_write

if TYPE_CHECKING:
    from .project import Project


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


def _environment(env: Mapping[str, str] | None) -> dict[str, str]:
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
    if "HIRAKU_JOB_ID" in additions:
        raise ValidationError("Environment cannot override reserved run attribution")
    return additions


class Job(BaseModel):
    """Execution identity and final outcome, with live PSI/J status in memory."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    record_id: str = Field(min_length=1)
    spec: psij.JobSpec
    status: psij.JobStatus = Field(
        default_factory=lambda: psij.JobStatus(psij.JobState.NEW)
    )
    executor: str = Field(default="detached", min_length=1)
    executor_url: str | None = None
    native_id: str | None = None
    hostname: str = Field(default_factory=socket.gethostname)
    commit: str | None = None
    git_state: Literal["clean", "dirty", "unavailable"] = "unavailable"
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    submitted_at: AwareDatetime | None = None
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None
    _path: Path = PrivateAttr()
    _env: dict[str, str] = PrivateAttr(default_factory=dict)
    _observer: psij.Job | None = PrivateAttr(default=None)

    @field_validator("spec", mode="before")
    @classmethod
    def read_spec(cls, value: Any) -> psij.JobSpec:
        if isinstance(value, dict):
            value = dict(value)
            if value.get("attributes") is not None:
                attrs = dict(value["attributes"])
                if "duration" in attrs:
                    attrs["duration"] = timedelta(seconds=attrs["duration"])
                value["attributes"] = psij.JobAttributes(**attrs)
            if value.get("resources") is not None:
                resources = dict(value["resources"])
                if resources.pop("__version") != 1:
                    raise ValueError("Unsupported PSI/J resource specification version")
                value["resources"] = psij.ResourceSpecV1(**resources)
            value = psij.JobSpec(**value)
        if not isinstance(value, psij.JobSpec):
            raise ValueError("spec must be a PSI/J JobSpec")
        validate_command([value.executable, *(value.arguments or [])])
        return value

    @field_serializer("spec")
    def write_spec(self, spec: psij.JobSpec) -> dict[str, Any]:
        # PSI/J's decoder currently prints field names; use public constructors above.
        data = json.loads(JSONSerializer().dumps(spec))
        if data.get("attributes") is not None:
            # Avoid PSI/J's deprecated alias and broken decimal-walltime decoder.
            data["attributes"].pop("project_name", None)
            data["attributes"]["duration"] = spec.attributes.duration.total_seconds()
        return data

    @field_validator("status", mode="before")
    @classmethod
    def read_status(cls, value: Any) -> psij.JobStatus:
        if isinstance(value, dict):
            value = dict(value)
            value["state"] = psij.JobState[value["state"]]
            return psij.JobStatus(**value)
        return value

    @field_serializer("status")
    def write_status(self, status: psij.JobStatus) -> dict[str, Any]:
        return {
            "state": status.state.name,
            "time": status.time,
            "exit_code": status.exit_code,
            "message": status.message,
            "metadata": status.metadata,
        }

    def _save(self) -> None:
        self.updated_at = datetime.now(UTC)
        atomic_write(self._path, self.model_dump_json(indent=2))

    @classmethod
    def _read(cls, path: Path) -> Job:
        try:
            job = cls.model_validate_json(path.read_text(encoding="utf-8"))
            if job.id != path.parent.name:
                raise ValueError("snapshot ID differs from directory")
            job._path = path
            return job
        except FileNotFoundError as exc:
            raise JobNotFoundError(f"No job at {path}") from exc
        except (OSError, ValueError, TypeError, KeyError, ValidationError) as exc:
            raise ExecutionError(f"Cannot read job at {path}: {exc}") from exc

    @classmethod
    def load(cls, job_id: str, *, project: Project) -> Job:
        """Load execution identity and any saved final outcome without attaching."""
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise JobNotFoundError(f"Invalid job ID: {job_id!r}")
        return cls._read(project.root / ".hiraku" / "jobs" / job_id / "job.json")

    @classmethod
    def list(cls, *, project: Project, record_id: str | None = None) -> list[Job]:
        """List saved attempts, optionally selecting one record."""
        jobs = (
            cls._read(path)
            for path in (project.root / ".hiraku" / "jobs").glob("*/job.json")
        )
        return sorted(
            (job for job in jobs if record_id is None or job.record_id == record_id),
            key=lambda job: (job.created_at, job.id),
        )

    @classmethod
    def create(
        cls,
        command: Sequence[str],
        *,
        project: Project,
        record_id: str | None = None,
        env: Mapping[str, str] | None = None,
        executor: str = "detached",
        executor_url: str | None = None,
        attributes: psij.JobAttributes | None = None,
        resources: psij.ResourceSpec | None = None,
        directory: Path | None = None,
    ) -> Job:
        """Validate and persist a new attempt before execution."""
        if executor == "local":
            executor = "detached"
        argv = validate_command(command)
        if argv is None:
            raise ValidationError("command must be an argv sequence")
        selected = record_id or project.active_record_id
        if selected is None:
            raise NoActiveRecordError(
                "Select a record with --record or hiraku record set"
            )
        record = project.vault.load(selected)
        additions = _environment(env)
        job_id = uuid4().hex
        job_directory = project.root / ".hiraku" / "jobs" / job_id
        job = cls(
            id=job_id,
            record_id=record.id,
            executor=executor,
            executor_url=(
                executor_url or str(project.root / ".hiraku" / "executors" / "detached")
                if executor == "detached"
                else executor_url
            ),
            spec=psij.JobSpec(
                executable=argv[0],
                arguments=argv[1:],
                directory=directory
                or (project.root if executor == "detached" else None),
                attributes=attributes,
                resources=resources,
                environment={"HIRAKU_JOB_ID": job_id},
                stdin_path=Path(os.devnull) if executor == "detached" else None,
                stdout_path=job_directory / "stdout.log",
                stderr_path=job_directory / "stderr.log",
            ),
        )
        job._path = job_directory / "job.json"
        with suppress(GitError):
            repository = GitRepository(project.root)
            job.commit = repository.resolve("HEAD")
            job.git_state = "dirty" if repository.is_dirty else "clean"
        try:
            job_directory.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            raise ExecutionError(f"Cannot create job {job.id}: {exc}") from exc
        job._env = additions
        job._save()
        return job

    def run(self) -> Job:
        """Submit and wait using the configured executor; cancel on interruption."""
        self.submit()
        try:
            self.wait()
        except BaseException:
            self.cancel()
            self.wait()
            raise
        return self

    def submit(self) -> Job:
        """Submit this attempt once through its configured PSI/J executor."""
        with FileLock(str(self._path.with_suffix(".lock"))):
            self._reload()
            if (
                self.status.state != psij.JobState.NEW
                or self.native_id is not None
                or self.submitted_at is not None
            ):
                raise ExecutionError(f"Job {self.id} has already been submitted")
            spec = copy(self.spec)
            spec.environment = {**(spec.environment or {}), **self._env}
            native = psij.Job(spec)
            try:
                self._executor().submit(native)
                self.native_id = str(native.native_id)
                self.submitted_at = datetime.now(UTC)
                self._set_status(native.status)
                self._observer = native
            except Exception as exc:
                self._set_status(psij.JobStatus(psij.JobState.FAILED, message=str(exc)))
                self._save()
                raise ExecutionError(f"Could not submit job {self.id}: {exc}") from exc
            self._save()
        return self

    def _executor(self) -> psij.JobExecutor:
        return psij.JobExecutor.get_instance(self.executor, url=self.executor_url)

    def _reload(self) -> None:
        """Reload the last saved observation."""
        updated = self._read(self._path)
        for field in type(self).model_fields:
            setattr(self, field, getattr(updated, field))

    def update(self) -> None:
        """Read the executor's latest observation; persist only final outcomes."""
        if (self.status.final and self._observer is None) or self.native_id is None:
            return
        try:
            if self._observer is None:
                self._reload()
                if self.status.final:
                    return
                native = psij.Job(self.spec)
                self._executor().attach(native, self.native_id)
                self._observer = native
            status = self._observer.wait(
                timeout=timedelta(seconds=30),
                target_states=[psij.JobState.QUEUED],
            )
            if status is None:
                raise ExecutionError("No backend observation; outcome unknown")
            self._set_status(status)
            if status.final:
                with FileLock(str(self._path.with_suffix(".lock")), timeout=1):
                    if not self._read(self._path).status.final:
                        self._save()
                self._observer = None
        except Timeout as exc:
            raise ExecutionError("Could not save final outcome: job is busy") from exc
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            psij.SubmitException,
            psij.InvalidJobException,
        ) as exc:
            raise ExecutionError(f"Could not update job {self.id}: {exc}") from exc

    def _set_status(self, status: psij.JobStatus) -> None:
        self.status = status
        started = (status.metadata or {}).get("started_at")
        if self.started_at is None:
            if started is not None:
                self.started_at = datetime.fromtimestamp(started, UTC)
            elif status.state == psij.JobState.ACTIVE:
                self.started_at = datetime.fromtimestamp(status.time, UTC)
        if status.final:
            self.finished_at = datetime.fromtimestamp(status.time, UTC)

    def _poll(self) -> Iterator[psij.JobStatus]:
        if self.native_id is None and not self.status.final:
            raise ExecutionError("Job has no native ID to observe")
        while True:
            self.update()
            yield self.status
            if self.status.final:
                return
            time.sleep(1)

    def wait(self) -> psij.JobStatus:
        """Wait for completion without displaying output or canceling on Ctrl-C."""
        for _ in self._poll():
            pass
        return self.status

    def cancel(self) -> None:
        """Request cancellation through PSI/J attachment."""
        self.update()
        if not self.status.final:
            if self._observer is None:
                raise ExecutionError("Job has no native ID to cancel")
            self._observer.cancel()
            self.update()

    def logs(self, *, follow: bool = False) -> Iterator[tuple[str, str]]:
        """Yield saved (stream name, text) pairs, optionally until completion."""
        yield from self._logs(
            self._poll() if follow else (self.status,), flush=not follow
        )

    def _logs(
        self,
        statuses: Sequence[psij.JobStatus] | Iterator[psij.JobStatus],
        *,
        flush: bool = False,
    ) -> Iterator[tuple[str, str]]:
        offsets = {"stdout": 0, "stderr": 0}
        decoders = {
            name: codecs.getincrementaldecoder("utf-8")(errors="replace")
            for name in offsets
        }
        for status in statuses:
            for name in offsets:
                path = self._path.parent / f"{name}.log"
                data = b""
                if path.exists():
                    with path.open("rb") as source:
                        source.seek(offsets[name])
                        data = source.read()
                        offsets[name] = source.tell()
                yield (
                    name,
                    decoders[name].decode(data, final=status.final or flush),
                )


def update_jobs(jobs: Sequence[Job]) -> None:
    """Attempt each update independently, preserving snapshots on failure."""
    for job in jobs:
        try:
            job.update()
        except ExecutionError as exc:
            logging.getLogger(__name__).warning(
                "Could not update job %s: %s", job.id, exc
            )
