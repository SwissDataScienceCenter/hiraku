"""Local PSI/J jobs with atomic JSON snapshots and saved output."""

from __future__ import annotations

import codecs
import json
import os
import re
import socket
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

import psij
import psutil
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


class Job(BaseModel):
    """A persisted Hiraku job execution wrapping PSI/J's specification and status."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    record_id: str = Field(min_length=1)
    spec: psij.JobSpec
    status: psij.JobStatus = Field(
        default_factory=lambda: psij.JobStatus(psij.JobState.NEW)
    )
    executor: str = Field(default="local", min_length=1)
    executor_url: str | None = None
    native_id: str | None = None
    hostname: str = Field(default_factory=socket.gethostname)
    commit: str | None = None
    git_state: Literal["clean", "dirty", "unavailable"] = "unavailable"
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    submitted_at: AwareDatetime | None = None
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    heartbeat_at: AwareDatetime | None = None
    _path: Path = PrivateAttr()

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
        """Load the last recorded observation without attaching to a native PID."""
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
    def submit(
        cls,
        command: Sequence[str],
        *,
        project: Project,
        record_id: str | None = None,
        env: Mapping[str, str] | None = None,
        executor: str = "local",
        executor_url: str | None = None,
        attributes: psij.JobAttributes | None = None,
        resources: psij.ResourceSpec | None = None,
        directory: Path | None = None,
    ) -> Job:
        """Persist an attempt and return after the PSI/J executor accepts it."""
        argv = validate_command(command)
        if argv is None:
            raise ValidationError("command must be an argv sequence")
        selected = record_id or project.active_record_id
        if selected is None:
            raise NoActiveRecordError(
                "Select a record with --record or hiraku record set"
            )
        record = project.vault.load(selected)
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
            raise ValidationError(
                "Environment cannot override reserved run attribution"
            )
        job_id = uuid4().hex
        job_directory = project.root / ".hiraku" / "jobs" / job_id
        job = cls(
            id=job_id,
            record_id=record.id,
            executor=executor,
            executor_url=executor_url,
            spec=psij.JobSpec(
                executable=argv[0],
                arguments=argv[1:],
                directory=directory or (project.root if executor == "local" else None),
                attributes=attributes,
                resources=resources,
                environment={"HIRAKU_JOB_ID": job_id},
                stdin_path=Path(os.devnull) if executor == "local" else None,
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
        try:
            job._save()
            with (job_directory / "worker.log").open("ab") as log:
                worker = subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        "import sys; from hiraku.jobs import Job; "
                        "Job._work(sys.argv[1])",
                        str(job._path),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    env={
                        **{k: v for k, v in os.environ.items() if k != "HIRAKU_JOB_ID"},
                        **additions,
                    },
                    start_new_session=True,
                )
        except OSError as exc:
            job.status = psij.JobStatus(psij.JobState.FAILED, message=str(exc))
            job.finished_at = datetime.now(UTC)
            if job._path.exists():
                job._save()
            raise ExecutionError(f"Could not submit job {job.id}: {exc}") from exc
        deadline = time.monotonic() + 30
        try:
            while True:
                job._reload()
                if job.submitted_at is not None or job.status.final:
                    return job
                if worker.poll() is not None:
                    raise ExecutionError(
                        f"Worker exited; see {job_directory / 'worker.log'}"
                    )
                if time.monotonic() >= deadline:
                    job.cancel()
                    raise ExecutionError(f"Submission timed out for job {job.id}")
                time.sleep(0.05)
        except KeyboardInterrupt:
            job.cancel()
            job.wait()
            raise

    def _reload(self) -> None:
        """Reload the last saved observation."""
        updated = self._read(self._path)
        for field in type(self).model_fields:
            setattr(self, field, getattr(updated, field))

    def update(self) -> None:
        """Persist a fresh observation, raising ExecutionError if unavailable."""
        self._reload()
        if self.status.final:
            return
        if self.executor == "local":
            observed = self.heartbeat_at or self.created_at
            if (datetime.now(UTC) - observed).total_seconds() > 30:
                raise ExecutionError("Local owner lost; outcome unknown")
            return
        try:
            with FileLock(str(self._path.with_suffix(".lock")), timeout=1):
                self._reload()
                if not self.status.final:
                    self._update(self._observation())
                    self._save()
        except Timeout as exc:
            raise ExecutionError("Job observation is busy; outcome unknown") from exc

    def _update(self, status: psij.JobStatus) -> None:
        self.status = status
        if status.state == psij.JobState.ACTIVE and self.started_at is None:
            self.started_at = datetime.fromtimestamp(status.time, UTC)
        if status.final:
            self.finished_at = datetime.fromtimestamp(status.time, UTC)

    def _observation(self) -> psij.JobStatus:
        """Use a short-lived attachment so executor polling threads cannot leak."""
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import sys; from hiraku.jobs import Job; "
                    "Job._observe(sys.argv[1])",
                    str(self._path),
                ],
                capture_output=True,
                text=True,
                timeout=40,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ExecutionError(f"Could not observe job {self.id}: {exc}") from exc
        if result.returncode:
            detail = result.stderr.strip().splitlines()
            raise ExecutionError(detail[-1] if detail else "Could not observe job")
        try:
            return self.read_status(json.loads(result.stdout))
        except (ValueError, KeyError, TypeError) as exc:
            raise ExecutionError("Invalid backend observation") from exc

    @classmethod
    def _observe(cls, path: str) -> None:
        job = cls._read(Path(path))
        if job.native_id is None:
            raise ExecutionError("Job has no native ID to observe")
        executor = psij.JobExecutor.get_instance(job.executor, url=job.executor_url)
        native = psij.Job(job.spec)
        executor.attach(native, job.native_id)
        if (job._path.parent / "cancel").exists():
            native.cancel()
        status = native.wait(
            timeout=timedelta(seconds=30), target_states=[psij.JobState.QUEUED]
        )
        if status is None:
            raise ExecutionError("No fresh backend observation; outcome unknown")
        print(json.dumps(job.write_status(status)))

    def _poll(self) -> Iterator[psij.JobStatus]:
        while True:
            self.update()
            yield self.status
            if self.status.final:
                return
            time.sleep(0.1 if self.executor == "local" else 5)

    def wait(self) -> psij.JobStatus:
        """Wait for completion without displaying output or canceling on Ctrl-C."""
        for _ in self._poll():
            pass
        return self.status

    def cancel(self) -> None:
        """Request cancellation through the owner or a remote attachment."""
        self._reload()
        if not self.status.final:
            (self._path.parent / "cancel").touch()
            if self.executor != "local":
                self.update()

    def logs(self, *, follow: bool = False) -> Iterator[tuple[str, str]]:
        """Yield saved (stream name, text) pairs, optionally until completion."""
        offsets = {"stdout": 0, "stderr": 0}
        decoders = {
            name: codecs.getincrementaldecoder("utf-8")(errors="replace")
            for name in offsets
        }
        for status in self._poll() if follow else (self.status,):
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
                    decoders[name].decode(data, final=status.final or not follow),
                )

    @classmethod
    def _work(cls, path: str) -> None:
        with FileLock(str(Path(path).with_suffix(".lock"))):
            cls._own(path)

    @classmethod
    def _own(cls, path: str) -> None:
        """Own the original PSI/J handle until completion, including in background."""
        job = cls._read(Path(path))
        native = psij.Job(job.spec)
        process = None

        def observe_start(_job: psij.Job, status: psij.JobStatus) -> None:
            if status.state == psij.JobState.ACTIVE:
                job.started_at = datetime.fromtimestamp(status.time, UTC)

        native.set_job_status_callback(observe_start)
        try:
            psij.JobExecutor.get_instance(job.executor, url=job.executor_url).submit(
                native
            )
            if job.executor == "local":
                with suppress(psutil.NoSuchProcess):
                    process = psutil.Process(int(native.native_id))
            job.native_id = str(native.native_id)
            job.submitted_at = datetime.now(UTC)
            job._update(native.status)
            job.heartbeat_at = datetime.now(UTC)
            job._save()
            if job.executor != "local":
                return
            while True:
                if (job._path.parent / "cancel").exists() and not native.status.final:
                    native.cancel()
                    # PSI/J reports CANCELED before its reaper kills the child.
                    if process is not None:
                        process.wait(timeout=10)
                native.wait(timeout=timedelta(seconds=0.2))
                job._update(native.status)
                job.heartbeat_at = datetime.now(UTC)
                if job.status.final:
                    job.finished_at = datetime.now(UTC)
                job._save()
                if job.status.final:
                    return
        except Exception as exc:
            if native.native_id is not None and not native.status.final:
                native.cancel()
                if process is not None:
                    with suppress(psutil.TimeoutExpired, psutil.NoSuchProcess):
                        process.wait(timeout=10)
            job.status = psij.JobStatus(psij.JobState.FAILED, message=str(exc))
            job.finished_at = datetime.now(UTC)
            job._save()
