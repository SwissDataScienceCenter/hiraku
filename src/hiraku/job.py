"""Research attribution and foreground output for native PSI/J jobs."""

from __future__ import annotations

import codecs
import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
from contextlib import ExitStack, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any
from uuid import UUID

import psij
import psutil
from psij.serialize import JSONSerializer

from .errors import ExecutionError, ValidationError
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
                submission = save_submission(job, project=project)
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
                _save_final_status(submission, job.status, project=project)
    return job.status


def describe_job(job: psij.Job) -> dict[str, Any]:
    """Expose native identity and status to CLI and MCP clients."""
    return {
        "executor": job.executor.name,
        "executor_url": job.executor.url,
        "native_id": job.native_id,
        "status": _status_data(job.status),
    }


def _status_data(status: psij.JobStatus) -> dict[str, Any]:
    return {
        "state": status.state.name,
        "time": status.time,
        "exit_code": status.exit_code,
        "message": status.message,
        "metadata": status.metadata,
    }


def _submission_path(job_id: str, project: Project) -> Path:
    try:
        if str(UUID(job_id)) != job_id:
            raise ValueError("Expected a canonical PSI/J UUID")
    except (ValueError, TypeError, AttributeError) as exc:
        raise ExecutionError(f"Invalid job ID: {job_id!r}") from exc
    return project.root / ".hiraku" / "jobs" / f"{job_id}.json"


def _write_submission(entry: dict[str, Any], project: Project) -> None:
    data = {
        key: value
        for key, value in entry.items()
        if key not in {"status", "observation_error"}
    }
    try:
        atomic_write(
            _submission_path(entry["id"], project),
            json.dumps(data, indent=2),
            mode=0o600,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise ExecutionError(
            f"Cannot save history for job {entry['id']} "
            f"({entry['executor']}, native ID {entry['native_id']}): {exc}. "
            "The job was submitted; do not resubmit automatically."
        ) from exc


def save_submission(job: psij.Job, *, project: Project) -> dict[str, Any]:
    """Record an accepted native job, keyed by its original PSI/J ID."""
    if job.executor is None or job.native_id is None or job.spec is None:
        raise ExecutionError("Only submitted jobs can be recorded")
    try:
        spec = json.loads(JSONSerializer().dumps(job.spec))
    except (TypeError, ValueError) as exc:
        raise ExecutionError(
            f"Job {job.id} was submitted (native ID {job.native_id}), "
            f"but its specification could not be serialized: {exc}"
        ) from exc
    status = job.status
    entry = {
        "id": job.id,
        "record_id": (job.spec.environment or {}).get("HIRAKU_RECORD_ID") or None,
        "submitted_at": datetime.now(UTC).isoformat(),
        "executor": job.executor.name,
        "executor_url": job.executor.url,
        "native_id": job.native_id,
        "spec": spec,
        "final_status": _status_data(status) if status.final else None,
    }
    _write_submission(entry, project)
    return {**entry, "status": _status_data(status), "observation_error": None}


def load_submission(job_id: str, *, project: Project) -> dict[str, Any]:
    """Read submission data without reconstructing jobs or executors."""
    path = _submission_path(job_id, project)
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
        if entry["id"] != job_id:
            raise ValueError("History ID differs from filename")
        if not isinstance(entry["spec"].get("arguments", []), list):
            raise ValueError("Command arguments must be a list")
        validate_command(
            [entry["spec"]["executable"], *entry["spec"].get("arguments", [])]
        )
        if not isinstance(entry["executor"], str) or not isinstance(
            entry["native_id"], str
        ):
            raise ValueError("Missing executor or native ID")
        datetime.fromisoformat(entry["submitted_at"])
        if entry["record_id"] is not None and not isinstance(entry["record_id"], str):
            raise ValueError("Invalid record ID")
        if entry["executor_url"] is not None and not isinstance(
            entry["executor_url"], str
        ):
            raise ValueError("Invalid executor URL")
        if (
            entry["final_status"] is not None
            and not psij.JobState[entry["final_status"]["state"]].final
        ):
            raise ValueError("Saved status must be final")
        return entry
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        ValidationError,
    ) as exc:
        raise ExecutionError(f"Cannot read history for job {job_id}: {exc}") from exc


def _save_final_status(
    entry: dict[str, Any], status: psij.JobStatus, *, project: Project
) -> None:
    entry["final_status"] = _status_data(status)
    _write_submission(entry, project)


def observe_submission(
    job_id: str, *, project: Project, wait: bool = False
) -> dict[str, Any]:
    """Retrieve native status, retaining final outcomes independently of backends."""
    return _observe_submission(load_submission(job_id, project=project), project, wait)


def _observe_submission(
    entry: dict[str, Any], project: Project, wait: bool = False
) -> dict[str, Any]:
    if entry["final_status"] is not None:
        return {**entry, "status": entry["final_status"], "observation_error": None}
    try:
        if entry["executor"] == "local":
            # Native local attachment cannot recover real exit codes.
            raise ExecutionError("Final status must be saved by the foreground process")
        executor = psij.JobExecutor.get_instance(
            entry["executor"], url=entry["executor_url"]
        )
        job = psij.Job()
        executor.attach(job, entry["native_id"])
        status = (
            job.wait()
            if wait
            else job.wait(
                timeout=timedelta(seconds=0.2), target_states=[psij.JobState.QUEUED]
            )
        ) or job.status
    except (
        psij.InvalidJobException,
        psij.SubmitException,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        ImportError,
        NotImplementedError,
        ExecutionError,
    ) as exc:
        return {
            **entry,
            "status": {
                "state": "UNKNOWN",
                "time": None,
                "exit_code": None,
                "message": None,
                "metadata": None,
            },
            "observation_error": str(exc),
        }
    if status.final:
        _save_final_status(entry, status, project=project)
    return {**entry, "status": _status_data(status), "observation_error": None}


def list_submissions(
    *, project: Project, record_id: str | None = None
) -> list[dict[str, Any]]:
    """List project submissions with refreshed or saved final status, newest first."""
    entries = [
        load_submission(path.stem, project=project)
        for path in (project.root / ".hiraku" / "jobs").glob("*.json")
    ]
    return [
        _observe_submission(entry, project)
        for entry in sorted(
            entries, key=lambda item: (item["submitted_at"], item["id"]), reverse=True
        )
        if record_id is None or entry["record_id"] == record_id
    ]


def _follow_streams(job: psij.Job) -> None:
    with ExitStack() as stack:
        streams = [
            [path, output, None, codecs.getincrementaldecoder("utf-8")("replace")]
            for path, output in (
                (job.spec.stdout_path, sys.stdout),
                (job.spec.stderr_path, sys.stderr),
            )
        ]
        while True:
            done = job.wait(timeout=timedelta(seconds=0.1)) is not None
            for stream in streams:
                path, output, file, decoder = stream
                if file is None and path.is_file():
                    file = stream[2] = stack.enter_context(path.open("rb"))
                if file is not None:
                    output.write(decoder.decode(file.read(), final=done))
                    output.flush()
            if done:
                break
