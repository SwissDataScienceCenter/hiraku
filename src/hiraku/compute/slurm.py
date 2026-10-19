"""Minimal Submitit-backed Slurm executor."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import submitit

from ..errors import JobError
from ..job import Job, JobSpec, _read
from .base import ComputeBackend


class SlurmBackend(ComputeBackend):
    """Submit a prepared Hiraku command to Slurm through Submitit.

    ``folder`` is Hiraku's local jobs directory. The Slurm log and Submitit
    metadata for each job are stored beneath that job's receipt directory.
    The snapshot and jobs directory must be visible from the Slurm compute
    nodes.
    """

    def __init__(self, folder: str | Path | None = None, **parameters: Any) -> None:
        if folder is None or not str(folder).strip():
            raise ValueError("SlurmBackend requires a folder to store job state")
        self.jobs_dir = Path(folder).expanduser().resolve()
        self.parameters = parameters

    def submit(self, spec: JobSpec) -> dict[str, object]:
        job_dir = self.jobs_dir / spec.job_id
        receipt = _read(job_dir)
        if receipt.source_path is None:
            raise JobError("Slurm jobs require a prepared source snapshot")

        command = list(spec.command)
        if spec.dispatcher:
            command = [str(receipt.root / spec.dispatcher), "--", *command]
        env = {
            **dict(spec.env),
            **_attribution(receipt),
        }
        folder = job_dir / "submitit"
        executor = self._executor(folder)
        job = executor.submit(
            _run_command,
            command,
            str(receipt.working_directory),
            env,
        )
        return {"folder": str(folder), "job_id": str(job.job_id)}

    def status(self, handle: dict[str, object]) -> dict[str, object]:
        try:
            job = self._job(handle)
            state = str(job.state).upper()
        except Exception:
            return {"status": "unknown"}

        if state in {"PENDING", "QUEUED", "REQUEUED"}:
            return {"status": "submitted"}
        if state in {"RUNNING", "SIGNALING", "COMPLETING"}:
            return {"status": "running"}
        if state in {"CANCELLED", "CANCELED"}:
            return {"status": "cancelled"}
        if state == "COMPLETED":
            try:
                exit_code = int(job.result())
            except Exception as exc:
                return {"status": "failed", "error": str(exc)}
            return {
                "status": "succeeded" if exit_code == 0 else "failed",
                "exit_code": exit_code,
            }
        if state in {"FAILED", "TIMEOUT", "NODE_FAIL", "OUT_OF_MEMORY"}:
            return {"status": "failed"}
        return {"status": "unknown"}

    def cancel(self, handle: dict[str, object]) -> None:
        job = self._job(handle)
        job.cancel(check=False)

    def logs(
        self, handle: dict[str, object], *, stream: str, follow: bool
    ) -> Iterator[str]:
        if stream not in {"stdout", "stderr"}:
            raise JobError(f"Unsupported log stream: {stream}")
        job = self._job(handle)
        path: Path = getattr(job.paths, stream)
        while not path.exists():
            if not follow or self.status(handle).get("status") in {
                "succeeded",
                "failed",
                "cancelled",
                "lost",
            }:
                return
            time.sleep(0.5)
        with path.open(encoding="utf-8", errors="replace") as output:
            while True:
                content = output.read()
                if content:
                    yield content
                if not follow or self.status(handle).get("status") in {
                    "succeeded",
                    "failed",
                    "cancelled",
                    "lost",
                }:
                    return
                time.sleep(0.5)

    def _executor(self, folder: Path) -> submitit.AutoExecutor:
        executor = submitit.AutoExecutor(folder=folder, cluster="slurm")
        if self.parameters:
            executor.update_parameters(**self.parameters)
        return executor

    def _job(self, handle: Mapping[str, object]) -> Any:
        folder = handle.get("folder")
        job_id = handle.get("job_id")
        if not isinstance(folder, str) or not isinstance(job_id, str):
            raise JobError("Invalid Slurm job handle")
        executor = self._executor(Path(folder))
        job_class = executor._executor.job_class
        return job_class(folder=folder, job_id=job_id)


def _run_command(command: list[str], cwd: str, additions: dict[str, str]) -> int:
    result = subprocess.run(
        command,
        cwd=cwd,
        env={**os.environ, **additions},
        check=False,
    )
    return result.returncode


def _attribution(job: Job) -> dict[str, str]:
    return {
        "HIRAKU_JOB_ID": job.id,
        "HIRAKU_JOB_DIR": str(job.directory),
        "HIRAKU_RECORD_ID": job.record_id,
        "HIRAKU_RECORD_FILE": str(job.record_file),
        "HIRAKU_ROOT": str(job.root),
        "HIRAKU_TRACKING_ROOT": str(job.tracking_root),
        "HIRAKU_COMMIT": job.commit,
        "HIRAKU_GIT_STATE": "dirty" if job.spec.source_dirty else "clean",
        "HIRAKU_SOURCE_DIGEST": job.spec.source_digest or "",
    }
