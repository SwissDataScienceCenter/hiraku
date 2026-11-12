"""Local PSI/J jobs with atomic JSON snapshots and saved output."""

from __future__ import annotations

import codecs
import json
import os
import re
import sys
import time
from collections.abc import Mapping, Sequence
from contextlib import ExitStack, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import psij
import psutil
from psij.serialize import JSONSerializer
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_serializer,
    field_validator,
    model_validator,
)

from .errors import (
    ExecutionError,
    JobNotFoundError,
    ValidationError,
)
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
    if {"HIRAKU_RECORD_ID", "HIRAKU_JOB_ID"} & additions.keys():
        raise ValidationError("Environment cannot override reserved run attribution")
    return additions


class Job(BaseModel):
    """Execution identity and an optional final outcome."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    record_id: str | None = Field(default=None, min_length=1)
    spec: psij.JobSpec
    status: psij.JobStatus = Field(
        default_factory=lambda: psij.JobStatus(psij.JobState.NEW)
    )
    executor: psij.JobExecutor | None = None
    native_id: str | None = None
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    submitted_at: AwareDatetime | None = None
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None
    path: Path = Field(exclude=True, repr=False)

    @model_validator(mode="before")
    @classmethod
    def read_executor(cls, value: Any) -> Any:
        if isinstance(value, dict):
            value = dict(value)
            url = value.pop("executor_url", None)
            if isinstance(value.get("executor"), str):
                value["executor"] = psij.JobExecutor.get_instance(
                    value["executor"], url=url
                )
        return value

    @field_serializer("executor")
    def write_executor(self, executor: psij.JobExecutor | None) -> str | None:
        return executor.name if executor is not None else None

    @computed_field
    @property
    def executor_url(self) -> str | None:
        return self.executor.url if self.executor is not None else None

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
        atomic_write(self.path, self.model_dump_json(indent=2))

    @classmethod
    def _read(cls, path: Path) -> Job:
        try:
            job = cls.model_validate(
                {**json.loads(path.read_text(encoding="utf-8")), "path": path}
            )
            if job.id != path.parent.name:
                raise ValueError("snapshot ID differs from directory")
            reader = getattr(job.executor, "read_result", None)
            result = reader(path.with_name("result.json")) if reader else None
            if result is not None:
                job._set_status(result)
            return job
        except FileNotFoundError as exc:
            raise JobNotFoundError(f"No job at {path}") from exc
        except (OSError, ValueError, TypeError, KeyError, ValidationError) as exc:
            raise ExecutionError(f"Cannot read job at {path}: {exc}") from exc

    @classmethod
    def load(cls, job_id: str, *, project: Project) -> Job:
        """Read saved execution metadata and the final outcome."""
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
        attributes: psij.JobAttributes | None = None,
        resources: psij.ResourceSpec | None = None,
        directory: Path | None = None,
    ) -> Job:
        """Validate and construct an unsaved execution attempt."""
        argv = validate_command(command)
        if argv is None:
            raise ValidationError("command must be an argv sequence")
        if record_id is not None:
            project.vault.load(record_id)
        env = _environment(env)
        job_id = uuid4().hex
        job_directory = project.root / ".hiraku" / "jobs" / job_id
        spec = psij.JobSpec(
            executable=argv[0],
            arguments=argv[1:],
            directory=directory,
            launcher="hiraku-single",
            attributes=attributes,
            resources=resources,
            environment={
                **env,
                "HIRAKU_RECORD_ID": record_id or "",
                "HIRAKU_JOB_ID": job_id,
            },
            stdin_path=Path(os.devnull),
            stdout_path=job_directory / "stdout.log",
            stderr_path=job_directory / "stderr.log",
        )
        return cls(
            id=job_id,
            record_id=record_id,
            spec=spec,
            path=job_directory / "job.json",
        )

    def run(self) -> Job:
        """Run locally through PSI/J and persist the native final status."""
        native = self.submit(executor="local")
        try:
            self._follow_streams(native)
            native.wait()
        except BaseException as exc:
            if not native.status.final:
                native.cancel()
                # PSI/J publishes cancellation before its reaper collects the child.
                with suppress(psutil.NoSuchProcess):
                    process = psutil.Process(int(native.native_id))
                    deadline = time.monotonic() + 10
                    while process.is_running():
                        if time.monotonic() >= deadline:
                            raise ExecutionError(
                                "Local cancellation timed out"
                            ) from exc
                        time.sleep(0.05)
            raise
        finally:
            self._set_status(native.status)
            self._save()
        return self

    def _follow_streams(self, native: psij.Job) -> None:
        with ExitStack() as stack:
            streams = [
                [
                    path,
                    output,
                    None,
                    codecs.getincrementaldecoder("utf-8")("replace"),
                ]
                for path, output in (
                    (self.spec.stdout_path, sys.stdout),
                    (self.spec.stderr_path, sys.stderr),
                )
            ]
            while True:
                done = native.wait(timeout=timedelta(seconds=0.1)) is not None
                for stream in streams:
                    path, output, file, decoder = stream
                    if file is None and path.is_file():
                        file = stream[2] = stack.enter_context(path.open("rb"))
                    if file is not None:
                        output.write(decoder.decode(file.read(), final=done))
                        output.flush()
                if done:
                    break

    def submit(self, *, executor: str | psij.JobExecutor) -> psij.Job:
        """Submit through the selected PSI/J executor and return its job."""
        if isinstance(executor, str):
            executor = psij.JobExecutor.get_instance(executor)
        if not isinstance(executor, psij.JobExecutor):
            raise ValidationError("executor must be a PSI/J executor name or instance")
        try:
            self.path.parent.mkdir(parents=True)
        except FileExistsError as exc:
            raise ExecutionError(f"Job {self.id} has already been submitted") from exc
        self.executor = executor
        self.status = psij.JobStatus(psij.JobState.QUEUED)
        self._save()
        try:
            native = psij.Job(self.spec)
            executor.submit(native)
            self.native_id = str(native.native_id)
            if not native.status.final:
                self._set_status(native.status)
            self.submitted_at = datetime.now(UTC)
            self._save()
        except Exception as exc:
            self._set_status(psij.JobStatus(psij.JobState.FAILED, message=str(exc)))
            self._save()
            raise ExecutionError(f"Could not submit job {self.id}: {exc}") from exc
        return native

    def _set_status(self, status: psij.JobStatus) -> None:
        self.status = status
        if self.started_at is None and status.state == psij.JobState.ACTIVE:
            self.started_at = datetime.fromtimestamp(status.time, UTC)
        if status.final:
            self.finished_at = datetime.fromtimestamp(status.time, UTC)
