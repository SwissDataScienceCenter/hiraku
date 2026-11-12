"""Research attribution and foreground output for native PSI/J jobs."""

from __future__ import annotations

import codecs
import os
import sys
import time
from collections.abc import Mapping, Sequence
from contextlib import ExitStack, suppress
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any

import psij
import psutil

from .errors import ExecutionError, ValidationError

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
        environment={
            **additions,
            "HIRAKU_RECORD_ID": record_id or "",
            "HIRAKU_JOB_ID": job.id,
        },
    )
    return job


def run_local(job: psij.Job) -> psij.JobStatus:
    """Submit locally, stream output, and return the native final status.

    Output uses temporary files unless the caller supplied output paths.
    """
    spec = job.spec
    if spec is None:
        raise ValidationError("A job specification is required")
    original = spec.stdout_path, spec.stderr_path
    with TemporaryDirectory(prefix="hiraku-output-") as temporary:
        spec.stdout_path = spec.stdout_path or Path(temporary) / "stdout"
        spec.stderr_path = spec.stderr_path or Path(temporary) / "stderr"
        spec.stdin_path = spec.stdin_path or Path(os.devnull)
        spec.launcher = spec.launcher or "quoted-single"
        try:
            psij.JobExecutor.get_instance("local").submit(job)
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
    return job.status


def describe_job(job: psij.Job) -> dict[str, Any]:
    """Expose native identity and status to CLI and MCP clients."""
    status = job.status
    return {
        "executor": job.executor.name,
        "executor_url": job.executor.url,
        "native_id": job.native_id,
        "status": {
            "state": status.state.name,
            "exit_code": status.exit_code,
            "message": status.message,
        },
    }


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
