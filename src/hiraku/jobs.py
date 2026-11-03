"""Local foreground jobs and the small executor interface Hiraku needs."""

from __future__ import annotations

import os
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from .errors import (
    ExecutionError,
    GitError,
    NoActiveRecordError,
    ValidationError,
)

if TYPE_CHECKING:
    from .config import HirakuConfig
    from .run import Run
    from .tracking.base import TrackingBackend


class JobState(StrEnum):
    """Lifecycle states for a foreground Hiraku job."""

    NEW = "new"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass
class JobStatus:
    """Current state and result of a local job execution."""

    state: JobState = JobState.NEW
    time: datetime = field(default_factory=lambda: datetime.now(UTC))
    message: str | None = None
    exit_code: int | None = None


@dataclass(frozen=True)
class JobSpec:
    """Command and environment needed to execute one local job."""

    executable: str
    arguments: Sequence[str] = ()
    directory: Path = Path(".")
    environment: Mapping[str, str] = field(default_factory=dict)
    inherit_environment: bool = True
    capture_output: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.executable, str) or not self.executable:
            raise ValidationError("Job executable must be a non-empty string")
        arguments = tuple(self.arguments)
        if any(not isinstance(arg, str) or "\x00" in arg for arg in arguments):
            raise ValidationError("Job arguments must be strings without NUL bytes")
        if "\x00" in self.executable:
            raise ValidationError("Job executable must not contain NUL bytes")
        if not isinstance(self.directory, Path):
            object.__setattr__(self, "directory", Path(self.directory))
        environment = dict(self.environment)
        if any(
            not isinstance(key, str)
            or not key
            or "=" in key
            or "\x00" in key
            or not isinstance(value, str)
            or "\x00" in value
            for key, value in environment.items()
        ):
            raise ValidationError("Job environment must map valid strings to strings")
        object.__setattr__(self, "arguments", arguments)
        object.__setattr__(self, "environment", MappingProxyType(environment))

    @property
    def command(self) -> list[str]:
        """Return the executable and arguments as an argv list."""
        return [self.executable, *self.arguments]


@dataclass
class Job:
    """One Hiraku command execution with record and Git provenance."""

    spec: JobSpec
    record_id: str | None = None
    commit: str | None = None
    git_state: str = "unavailable"
    record_file: Path | None = None
    id: str = field(default_factory=lambda: uuid4().hex)
    status: JobStatus = field(default_factory=JobStatus)

    def execute(
        self, executor: JobExecutor | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Execute this job with the selected executor, local by default."""
        selected = executor or LocalJobExecutor()
        return selected.execute(self)

    def attribution_environment(self) -> dict[str, str]:
        """Return the Hiraku metadata variables associated with this job."""
        values = {
            "HIRAKU_JOB_ID": self.id,
            "HIRAKU_COMMIT": self.commit or "",
            "HIRAKU_GIT_STATE": self.git_state,
            "HIRAKU_ROOT": str(self.spec.directory),
            "HIRAKU_TRACKING_ROOT": str(self.spec.directory),
        }
        if self.record_id is not None:
            values["HIRAKU_RECORD_ID"] = self.record_id
        if self.record_file is not None:
            values["HIRAKU_RECORD_FILE"] = str(self.record_file)
        return values

    def tracked_runs(self, backend: TrackingBackend) -> list[Run]:
        """Return tracked runs attributed to this job."""
        if self.record_id is None:
            return []
        return [
            run
            for run in backend.list_runs(record_id=self.record_id)
            if run.job_id == self.id
        ]


class JobExecutor(ABC):
    """Interface implemented by Hiraku command executors."""

    @abstractmethod
    def execute(self, job: Job) -> subprocess.CompletedProcess[str]:
        """Run a job and return its process result."""


class LocalJobExecutor(JobExecutor):
    """Execute a command directly in the foreground on the local machine."""

    def execute(self, job: Job) -> subprocess.CompletedProcess[str]:
        spec = job.spec
        environment = os.environ.copy() if spec.inherit_environment else {}
        for name in (
            "HIRAKU_JOB_ID",
            "HIRAKU_RECORD_ID",
            "HIRAKU_RECORD_FILE",
            "HIRAKU_ROOT",
            "HIRAKU_TRACKING_ROOT",
            "HIRAKU_COMMIT",
            "HIRAKU_GIT_STATE",
        ):
            environment.pop(name, None)
        environment.update(spec.environment)
        environment.update(job.attribution_environment())
        job.status = JobStatus(state=JobState.ACTIVE)
        try:
            result = subprocess.run(
                spec.command,
                cwd=spec.directory,
                env=environment,
                check=False,
                capture_output=spec.capture_output,
                text=True,
            )
        except KeyboardInterrupt:
            job.status = JobStatus(
                state=JobState.CANCELED,
                message="Interrupted by user",
            )
            raise
        except OSError as exc:
            job.status = JobStatus(state=JobState.FAILED, message=str(exc))
            raise ExecutionError(
                f"Could not execute command {spec.executable!r}: {exc}"
            ) from exc

        state = JobState.COMPLETED if result.returncode == 0 else JobState.FAILED
        job.status = JobStatus(state=state, exit_code=result.returncode)
        return result


def validate_command(value: Any) -> list[str] | None:
    """Validate command configuration as a literal argv sequence."""
    if value is None:
        return None
    if isinstance(value, str | bytes) or not isinstance(value, list | tuple):
        raise ValidationError("command must be an argv sequence")
    command = list(value)
    if not command or not isinstance(command[0], str) or not command[0]:
        raise ValidationError("command executable must be a non-empty string")
    if any(not isinstance(argument, str) or "\x00" in argument for argument in command):
        raise ValidationError("command arguments must be strings without NUL bytes")
    return command


def execute_command(
    command: Sequence[str],
    *,
    record_id: str | None = None,
    dispatch: str | None = None,
    env: Mapping[str, str] | None = None,
    capture_output: bool = False,
    config: HirakuConfig | None = None,
) -> subprocess.CompletedProcess[str]:
    """Prepare and execute a local job in the current project."""
    from .config import HirakuConfig as Config
    from .git import GitRepository
    from .record import load_record

    selected = config or Config.discover()
    selected_record = record_id or selected.active_record_id
    if selected_record is None:
        raise NoActiveRecordError("Select a record with --record or hiraku record set")
    record = load_record(selected_record, config=selected)
    if isinstance(command, str | bytes):
        raise ValidationError("command must be an argv sequence, not a shell string")
    argv = validate_command(list(command) if command is not None else None)
    if argv is None:
        raise ValidationError("command must be a non-empty argv sequence")

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
        "HIRAKU_RECORD_ID",
        "HIRAKU_JOB_ID",
        "HIRAKU_RECORD_FILE",
        "HIRAKU_ROOT",
        "HIRAKU_TRACKING_ROOT",
        "HIRAKU_COMMIT",
        "HIRAKU_GIT_STATE",
        "HIRAKU_SOURCE_DIGEST",
        "HIRAKU_ENTRYPOINT",
    }
    if reserved.intersection(additions):
        raise ValidationError("Environment cannot override reserved run attribution")

    tracking_spec = record.tracking_backend_spec(config=selected)
    if tracking_spec.get("type") == "wandb":
        wandb_project = tracking_spec.get("project") or selected.root.name
        if (
            isinstance(wandb_project, str)
            and wandb_project
            and "WANDB_PROJECT" not in additions
            and "WANDB_PROJECT" not in os.environ
        ):
            additions["WANDB_PROJECT"] = wandb_project
    elif tracking_spec.get("type") == "mlflow":
        from .tracking import create_backend

        tracking_backend = create_backend(tracking_spec, selected)
        if (
            "MLFLOW_TRACKING_URI" not in additions
            and "MLFLOW_TRACKING_URI" not in os.environ
        ):
            additions["MLFLOW_TRACKING_URI"] = tracking_backend.tracking_uri
        if (
            "MLFLOW_EXPERIMENT_NAME" not in additions
            and "MLFLOW_EXPERIMENT_NAME" not in os.environ
        ):
            additions["MLFLOW_EXPERIMENT_NAME"] = (
                tracking_backend.experiment_name or selected.root.name
            )

    commit = ""
    git_state = "unavailable"
    try:
        repository = GitRepository(selected.root)
        commit = repository.resolve("HEAD")
        git_state = "dirty" if repository.is_dirty else "clean"
    except GitError:
        pass

    job_id = uuid4().hex
    environment = os.environ.copy()
    for name in reserved:
        environment.pop(name, None)
    environment.update(additions)

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
            dispatcher_name if Path(dispatcher_name).suffix else f"{dispatcher_name}.sh"
        )
        dispatcher_path = selected.dispatch_dir / filename
        if dispatch is not None and not dispatcher_path.is_file():
            raise ExecutionError(f"Dispatcher does not exist: {dispatcher_path}")
        if dispatcher_path.is_file():
            argv = [str(dispatcher_path), "--", *argv]

    job = Job(
        spec=JobSpec(
            executable=argv[0],
            arguments=argv[1:],
            directory=selected.root,
            environment=environment,
            inherit_environment=False,
            capture_output=capture_output,
        ),
        record_id=record.id,
        commit=commit or None,
        git_state=git_state,
        record_file=record.path,
        id=job_id,
    )
    return LocalJobExecutor().execute(job)
