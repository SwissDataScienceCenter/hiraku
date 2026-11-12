"""PSI/J local execution with detached, durable outcomes."""

import logging
import os
import re
import subprocess
import sys
import time
import weakref
from pathlib import Path
from threading import Thread
from urllib.parse import unquote, urlsplit

from packaging.version import Version
from psij import (
    InvalidJobException,
    Job,
    JobExecutor,
    JobExecutorConfig,
    JobState,
    JobStatus,
    ResourceSpecV1,
    SubmitException,
)
from psij.descriptor import Descriptor
from psij.launchers.script_based_launcher import ScriptBasedLauncher
from pydantic import AwareDatetime, BaseModel, Field

logger = logging.getLogger(__name__)


class _Result(BaseModel):
    exit_code: int | None = Field(strict=True)
    finished_at: AwareDatetime
    message: str | None = None


class LocalDetachedJobExecutor(JobExecutor):
    """Run a PSI/J launcher in a detached supervisor and observe its saved result."""

    def __init__(self, url: str | None = None, config: JobExecutorConfig | None = None):
        config = config or JobExecutorConfig()
        parsed = urlsplit(
            url or (config.work_directory / "detached").absolute().as_uri()
        )
        if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
            raise ValueError("Detached execution requires a jobs-directory file URL")
        self.jobs_dir = Path(unquote(parsed.path))
        super().__init__(url=self.jobs_dir.as_uri(), config=config)

    def submit(self, job: Job) -> None:
        if os.name != "posix":
            raise InvalidJobException("Detached execution requires a POSIX system")
        if job.spec is None:
            raise InvalidJobException("Detached execution requires a job spec")
        if job.spec.resources is not None and (
            not isinstance(job.spec.resources, ResourceSpecV1)
            or job.spec.resources.computed_process_count != 1
        ):
            raise InvalidJobException(
                "Detached execution supports a single local process"
            )
        spec = self._check_job(job)
        path = self._result_path(job.id)
        spec.stdout_path = spec.stdout_path or path.with_name("stdout.log")
        spec.stderr_path = spec.stderr_path or path.with_name("stderr.log")
        spec.stdin_path = spec.stdin_path or Path(os.devnull)
        spec.launcher = spec.launcher or "quoted-single"
        try:
            command = self._get_launcher(spec.launcher).get_launch_command(job)
            environment = os.environ.copy() if spec.inherit_environment else {}
            environment.update(
                {key: str(value) for key, value in (spec.environment or {}).items()}
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).with_name("_local_supervisor.py")),
                    str(path),
                    *command,
                ],
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=spec.directory,
                env=environment,
            )
        except Exception as exc:
            job.executor = None
            if isinstance(exc, InvalidJobException | SubmitException):
                raise
            raise SubmitException(
                "Could not start detached supervisor", exception=exc
            ) from exc
        job._native_id = job.id
        self._set_job_status(job, JobStatus(JobState.QUEUED))
        self._set_job_status(job, JobStatus(JobState.ACTIVE))
        Thread(
            target=self._watch, args=(weakref.ref(job), path, process), daemon=True
        ).start()

    def _result_path(self, native_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9-]{36}", native_id):
            raise InvalidJobException("Invalid detached job ID")
        return self.jobs_dir / native_id / "result.json"

    @staticmethod
    def _read_result(path: Path) -> JobStatus | None:
        try:
            result = _Result.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        return JobStatus(
            JobState.COMPLETED if result.exit_code == 0 else JobState.FAILED,
            time=result.finished_at.timestamp(),
            exit_code=result.exit_code,
            message=result.message,
        )

    def attach(self, job: Job, native_id: str) -> None:
        if job.status.state != JobState.NEW or job.executor is not None:
            raise InvalidJobException("Job must be new and unbound")
        path = self._result_path(native_id)
        if not path.parent.is_dir():
            raise InvalidJobException("No detached job with this ID")
        status = self._read_result(path)
        job.executor = self
        job._native_id = native_id
        self._set_job_status(job, status or JobStatus(JobState.QUEUED))
        if status is None:
            Thread(
                target=self._watch, args=(weakref.ref(job), path), daemon=True
            ).start()

    def list(self) -> list[str]:
        return sorted(
            path.name
            for path in self.jobs_dir.glob("*")
            if path.is_dir() and re.fullmatch(r"[a-f0-9-]{36}", path.name)
        )

    def _watch(
        self,
        reference: weakref.ReferenceType[Job],
        path: Path,
        process: subprocess.Popen | None = None,
    ) -> None:
        try:
            while True:
                if process is not None:
                    process.poll()
                job = reference()
                if job is None or job.status.final:
                    return
                try:
                    status = self._read_result(path)
                    if status is not None:
                        self._set_job_status(job, status)
                        return
                except (OSError, ValueError) as exc:
                    logger.warning("Cannot read detached job result %s: %s", path, exc)
                finally:
                    del job
                time.sleep(0.1)
        finally:
            # Reap our child even when the caller discards its PSI/J job.
            if process is not None:
                process.wait()

    def cancel(self, job: Job) -> None:
        raise SubmitException("Detached cancellation is not supported")


class QuotedSingleLauncher(ScriptBasedLauncher):
    """PSI/J's single launcher with quoted paths, including output redirections."""

    def __init__(self, config: JobExecutorConfig | None = None):
        super().__init__(
            Path(__file__).parent.parent / "hiraku/resources/launchers/single.sh",
            config,
        )


__PSI_J_LAUNCHERS__ = [
    Descriptor(
        name="quoted-single",
        version=Version("0.1.0"),
        cls="psij-descriptors.local.QuotedSingleLauncher",
    )
]

__PSI_J_EXECUTORS__ = [
    Descriptor(
        name="local-detached",
        version=Version("0.1.0"),
        cls="psij-descriptors.local.LocalDetachedJobExecutor",
    )
]
