"""PSI/J executor using the authenticated rnk CLI, without a Renku SDK."""

from __future__ import annotations

import hashlib
import json
import logging
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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

from hiraku.io import atomic_write

logger = logging.getLogger(__name__)


class RenkuJobExecutor(JobExecutor):
    """Use a Renku launcher for resources/image; cache combined logs locally."""

    def __init__(
        self,
        url: str | None = None,
        config: JobExecutorConfig | None = None,
    ) -> None:
        target = urlsplit(url or "https://renkulab.io")
        self._project = target.path.removeprefix("/p/").strip("/") or None
        if target.path.strip("/") and not target.path.startswith("/p/"):
            raise ValueError("Use a Renku server URL or /p/namespace/project URL")
        super().__init__(
            urlunsplit((target.scheme, target.netloc, "", "", "")),
            config or JobExecutorConfig(),
        )
        self._canceled: set[str] = set()
        self._observation_lock = threading.Lock()

    @staticmethod
    def _attributes(job: Job) -> dict[str, Any]:
        return (
            dict(job.spec.attributes.custom_attributes or {})
            if (job.spec and job.spec.attributes)
            else {}
        )

    def _rnk(
        self,
        *args: str,
        project: str | None = None,
        json_output: bool = True,
    ) -> Any:
        command = ["rnk", "--renku-url", self.url or "https://renkulab.io"]
        if json_output:
            command.extend(["--format", "json"])
        if project or self._project:
            command.extend(["--project-context", project or self._project])
        try:
            result = subprocess.run(
                [*command, *args],
                capture_output=True,
                text=True,
                timeout=20,
                check=True,
            )
            return json.loads(result.stdout) if json_output else result.stdout
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            detail = (
                exc.stderr.strip()
                if isinstance(exc, subprocess.CalledProcessError)
                else str(exc)
            )
            raise SubmitException(f"rnk {' '.join(args[:2])}: {detail}") from exc

    def submit(self, job: Job) -> None:
        attrs = self._attributes(job)
        if not attrs.get("renku.launcher") or not (
            attrs.get("renku.project") or self._project
        ):
            raise InvalidJobException("Set renku.launcher and renku.project attributes")
        if job.spec and job.spec.resources is not None:
            raise InvalidJobException("Renku resources are configured by the launcher")
        spec = self._check_job(job)
        if spec.pre_launch or spec.post_launch or spec.launcher:
            raise InvalidJobException(
                "Renku does not support PSI/J launch/staging hooks"
            )
        argv = [
            "env",
            *(f"{key}={value}" for key, value in (spec.environment or {}).items()),
            spec.executable,
            *(spec.arguments or []),
        ]
        if spec.directory:
            argv = [
                "sh",
                "-c",
                f"cd {shlex.quote(str(spec.directory))} && exec " + shlex.join(argv),
            ]
        # Preserve a required image entrypoint when explicitly configured.
        prefix = shlex.split(str(attrs.get("renku.command_prefix", "")))
        command = prefix + argv[:1]
        flags = [item for word in command for item in ("--command", word)]
        hiraku_id = str((spec.environment or {}).get("HIRAKU_JOB_ID", job.id))
        # rnk accepts 4–20 lowercase alphanumeric/dash characters, unlike a UUID.
        submission_id = "h" + hashlib.sha256(hiraku_id.encode()).hexdigest()[:19]
        result = self._rnk(
            "job",
            "start",
            "--launcher",
            str(attrs["renku.launcher"]),
            "--submission-id",
            submission_id,
            *flags,
            "--",
            *argv[1:],
            project=attrs.get("renku.project"),
        )
        job._native_id = result["name"]
        self._set_job_status(job, JobStatus(JobState.QUEUED))
        self._monitor(job)

    def list(self) -> list[str]:
        return [item["name"] for item in self._rnk("job", "list")]

    def attach(self, job: Job, native_id: str) -> None:
        if job.status.state != JobState.NEW or job.executor is not None:
            raise InvalidJobException("Attach requires an unsubmitted PSI/J Job")
        job.executor = self
        job._native_id = native_id
        self._monitor(job)

    def cancel(self, job: Job) -> None:
        if job.native_id not in self._canceled and not job.status.final:
            self._rnk(
                "job",
                "stop",
                str(job.native_id),
                json_output=False,
                project=self._attributes(job).get("renku.project"),
            )
            self._canceled.add(str(job.native_id))

    def _monitor(self, job: Job) -> None:
        def poll() -> None:
            while not job.status.final:
                try:
                    self.refresh(job)
                except Exception as exc:
                    # A transport failure is not evidence that execution failed.
                    logger.warning(
                        "Cannot observe Renku job %s: %s", job.native_id, exc
                    )
                if not job.status.final:
                    threading.Event().wait(5)

        threading.Thread(target=poll, name="renku-job-monitor", daemon=True).start()

    def refresh(self, job: Job) -> None:
        """Publish one real observation, serialized with background polling."""
        with self._observation_lock:
            self._observe(job)

    def _observe(self, job: Job) -> None:
        project = self._attributes(job).get("renku.project")
        observation = next(
            (
                item
                for item in self._rnk("job", "list", project=project)
                if item["name"] == job.native_id
            ),
            None,
        )
        if observation is None:
            if job.native_id in self._canceled:
                self._set_job_status(job, JobStatus(JobState.CANCELED))
                return
            raise SubmitException("Job missing from Renku listing; outcome unknown")
        state = observation["status"]["state"]
        states = {
            "starting": JobState.QUEUED,
            "running": JobState.ACTIVE,
            "succeeded": JobState.COMPLETED,
            "failed": JobState.FAILED,
        }
        if state == "stopping":
            return
        if state == "hibernated" and job.native_id in self._canceled:
            states[state] = JobState.CANCELED
        if state not in states:
            raise SubmitException(f"Unrecognized Renku state: {state}")
        if job.spec and job.spec.stdout_path:
            try:
                output = self._rnk("job", "logs", str(job.native_id), json_output=False)
                atomic_write(Path(job.spec.stdout_path), output)
            except SubmitException as exc:
                logger.warning("Cannot fetch Renku logs for %s: %s", job.native_id, exc)
        self._set_job_status(
            job,
            JobStatus(
                states[state],
                message=observation["status"].get("message"),
            ),
        )
        job.status.metadata = {"observed_at": time.time()}


__PSI_J_EXECUTORS__ = [
    Descriptor(
        name="renku",
        version=Version("0.1.0"),
        cls="psij-descriptors.renku.RenkuJobExecutor",
    )
]
