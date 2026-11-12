"""PSI/J launchers and detached local execution."""

import json
import logging
import os
import re
import sys
import time
import weakref
from pathlib import Path
from threading import Lock, Thread
from urllib.parse import unquote, urlsplit

from packaging.version import Version
from psij import (
    InvalidJobException,
    Job,
    JobExecutor,
    JobExecutorConfig,
    JobState,
    JobStatus,
    SubmitException,
)
from psij.descriptor import Descriptor
from psij.launchers.script_based_launcher import ScriptBasedLauncher
from pydantic import AwareDatetime, BaseModel, Field

logger = logging.getLogger(__name__)


class _Result(BaseModel):
    exit_code: int = Field(strict=True, ge=0, le=255)
    finished_at: AwareDatetime


class LocalDetachedJobExecutor(JobExecutor):
    """Run detached local jobs and attach to their saved results."""

    _watch_lock = Lock()
    _watching = False
    _attached = weakref.WeakKeyDictionary()

    def __init__(self, url: str | None = None, config: JobExecutorConfig | None = None):
        super().__init__(url=url, config=config or JobExecutorConfig())
        parsed = urlsplit(url or "")
        if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
            raise ValueError("Detached execution requires a jobs-directory file URL")
        self.jobs_dir = Path(unquote(parsed.path))
        self._local = JobExecutor.get_instance("local", config=self.config)
        self._submitted: dict[Job, Job] = {}

    def submit(self, job: Job) -> None:
        spec = job.spec
        if spec is None:
            raise InvalidJobException("Detached execution requires a job spec")
        job_id = (spec.environment or {}).get("HIRAKU_JOB_ID", "")
        result_path = self._result_path(str(job_id))
        if spec.stdout_path is None or spec.stdout_path.parent != result_path.parent:
            raise InvalidJobException("Detached stdout must be in the job directory")
        if spec.launcher not in (None, "detached"):
            raise InvalidJobException("Detached execution requires its launcher")
        self._check_job(job)
        spec.launcher = "detached"
        native = Job(spec)
        job._native_id = job_id
        self._submitted[job] = native

        def on_status(_job: Job, status: JobStatus) -> None:
            self._set_job_status(job, status)
            if status.final:
                self._submitted.pop(job, None)

        native.set_job_status_callback(on_status)
        try:
            self._local.submit(native)
        except Exception:
            self._submitted.pop(job, None)
            job.executor = None
            job._native_id = None
            raise

    def _result_path(self, native_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", native_id):
            raise InvalidJobException("Invalid detached job ID")
        return self.jobs_dir / native_id / "result.json"

    def attach(self, job: Job, native_id: str) -> None:
        if job.status.state != JobState.NEW or job.executor is not None:
            raise InvalidJobException("Job must be new and unbound")
        path = self._result_path(native_id)
        if not path.parent.is_dir():
            raise InvalidJobException("No detached job with this ID")
        try:
            result = _Result.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            result = None
        job.executor = self
        job._native_id = native_id
        if result is None:
            self._set_job_status(job, JobStatus(JobState.QUEUED))
            with self._watch_lock:
                self._attached[job] = (self, path)
                if not type(self)._watching:
                    type(self)._watching = True
                    Thread(target=type(self)._watch, daemon=True).start()
        else:
            self._publish_result(job, result)

    def list(self) -> list[str]:
        jobs = []
        for path in self.jobs_dir.glob("*/job.json"):
            try:
                snapshot = json.loads(path.read_text(encoding="utf-8"))
                if (
                    snapshot.get("executor") == "local-detached"
                    and snapshot.get("native_id") == path.parent.name
                ):
                    jobs.append(path.parent.name)
            except (OSError, ValueError, AttributeError):
                continue
        return sorted(jobs)

    @classmethod
    def _watch(cls) -> None:
        while True:
            with cls._watch_lock:
                attached = list(cls._attached.items())
                if not attached:
                    cls._watching = False
                    return
            for job, (executor, path) in attached:
                try:
                    result = _Result.model_validate_json(
                        path.read_text(encoding="utf-8")
                    )
                except FileNotFoundError:
                    continue
                except (OSError, ValueError) as exc:
                    logger.warning("Cannot read detached job result %s: %s", path, exc)
                else:
                    executor._publish_result(job, result)
                with cls._watch_lock:
                    cls._attached.pop(job, None)
            # Do not keep an otherwise unused attachment alive between polls.
            del attached, job, executor
            time.sleep(0.1)

    def _publish_result(self, job: Job, result: _Result) -> None:
        self._set_job_status(
            job,
            JobStatus(
                JobState.COMPLETED if result.exit_code == 0 else JobState.FAILED,
                time=result.finished_at.timestamp(),
                exit_code=result.exit_code,
            ),
        )

    def cancel(self, job: Job) -> None:
        native = self._submitted.get(job)
        if native is None:
            raise SubmitException("Cancellation after attachment is not supported")
        native.cancel()


class QuotedSingleLauncher(ScriptBasedLauncher):
    """PSI/J's single launcher with quoted paths, including output redirections."""

    def __init__(self, config: JobExecutorConfig | None = None):
        super().__init__(
            Path(__file__).parent.parent / "hiraku/resources/launchers" / self.script,
            config,
        )

    script = "single.sh"


class DetachedLauncher(QuotedSingleLauncher):
    script = "detached.sh"

    def get_additional_args(self, job: Job) -> list[str]:
        if os.name != "posix":
            raise InvalidJobException("Local recording requires a POSIX system")
        if job.spec.stdout_path is None:
            raise InvalidJobException("Local recording requires stdout_path")
        if job.spec.resources and job.spec.resources.computed_process_count != 1:
            raise InvalidJobException("Recording supports a single local process")
        return [str(Path(job.spec.stdout_path).with_name("result.json"))]

    def get_launch_command(self, job: Job) -> list[str]:
        return [
            sys.executable,
            "-c",
            "import os,sys; os.setsid(); os.execv(sys.argv[1], sys.argv[1:])",
            *super().get_launch_command(job),
        ]

    def is_launcher_failure(self, output: str) -> bool:
        # The detached launcher disconnects diagnostics from the submitter.
        return (
            bool(output.strip()) and "_PSI_J_LAUNCHER_DONE" not in output.splitlines()
        )


__PSI_J_LAUNCHERS__ = [
    Descriptor(
        name="quoted-single",
        version=Version("0.1.0"),
        cls="psij-descriptors.local.QuotedSingleLauncher",
    ),
    Descriptor(
        name="detached",
        version=Version("0.1.0"),
        cls="psij-descriptors.local.DetachedLauncher",
    ),
]

__PSI_J_EXECUTORS__ = [
    Descriptor(
        name="local-detached",
        version=Version("0.1.0"),
        cls="psij-descriptors.local.LocalDetachedJobExecutor",
    )
]
