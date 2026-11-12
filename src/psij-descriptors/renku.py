"""PSI/J executor using the authenticated rnk CLI, without a Renku SDK."""

from __future__ import annotations

import hashlib
import json
import logging
import shlex
import subprocess
import time
import weakref
from threading import Thread
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

logger = logging.getLogger(__name__)


class RenkuJobExecutor(JobExecutor):
    """Submit and observe Renku jobs through the authenticated rnk CLI."""

    poll_interval = 2.0

    def __init__(
        self,
        url: str | None = None,
        config: JobExecutorConfig | None = None,
    ) -> None:
        target = urlsplit(url or "")
        self._project = target.path.removeprefix("/p/").strip("/")
        if (
            target.scheme not in ("http", "https")
            or not target.netloc
            or not target.path.startswith("/p/")
            or len(self._project.split("/")) != 2
            or not all(self._project.split("/"))
        ):
            raise ValueError("Use a Renku /p/namespace/project URL")
        self._server_url = urlunsplit((target.scheme, target.netloc, "", "", ""))
        super().__init__(url, config or JobExecutorConfig())

    @staticmethod
    def _attributes(job: Job) -> dict[str, Any]:
        return (
            dict(job.spec.attributes.custom_attributes or {})
            if (job.spec and job.spec.attributes)
            else {}
        )

    def _rnk(self, *args: str) -> Any:
        command = [
            "rnk",
            "--renku-url",
            self._server_url,
            "--format",
            "json",
            "--project-context",
            self._project,
        ]
        try:
            result = subprocess.run(
                [*command, *args],
                capture_output=True,
                text=True,
                timeout=20,
                check=True,
            )
            return json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            detail = (
                exc.stderr.strip()
                if isinstance(exc, subprocess.CalledProcessError)
                else str(exc)
            )
            raise SubmitException(f"rnk {' '.join(args[:2])}: {detail}") from exc

    def submit(self, job: Job) -> None:
        attrs = self._attributes(job)
        if not attrs.get("renku.launcher"):
            raise InvalidJobException("Set the renku.launcher attribute")
        if job.spec and job.spec.resources is not None:
            raise InvalidJobException("Renku resources are configured by the launcher")
        spec = self._check_job(job)
        if spec.pre_launch or spec.post_launch or spec.launcher not in (None, "single"):
            raise InvalidJobException(
                "Renku does not support PSI/J launch/staging hooks"
            )
        if spec.stdin_path or spec.stdout_path or spec.stderr_path:
            raise InvalidJobException("Renku does not support PSI/J stream redirection")
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
        # rnk accepts 4–20 lowercase alphanumeric/dash characters, unlike a UUID.
        submission_id = "h" + hashlib.sha256(job.id.encode()).hexdigest()[:19]
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
        )
        job._native_id = result["name"]
        self._set_job_status(job, self._status(result))
        self._observe(job)

    def list(self) -> list[str]:
        return [item["name"] for item in self._rnk("job", "list")]

    def attach(self, job: Job, native_id: str) -> None:
        if job.status.state != JobState.NEW or job.executor is not None:
            raise InvalidJobException("Job must be new and unbound")
        status = self._lookup(native_id)
        job.executor = self
        job._native_id = native_id
        self._set_job_status(job, status)
        self._observe(job)

    @staticmethod
    def _status(result: dict[str, Any]) -> JobStatus:
        status = result["status"]
        states = {
            "starting": JobState.QUEUED,
            "running": JobState.ACTIVE,
            "stopping": JobState.ACTIVE,
            "succeeded": JobState.COMPLETED,
            "failed": JobState.FAILED,
            "hibernated": JobState.CANCELED,
        }
        state = states.get(status["state"])
        if state is None:
            raise SubmitException(f"Unknown Renku state: {status['state']}")
        # rnk exposes final states, but does not expose process exit codes.
        return JobStatus(state, message=status.get("message"))

    def _lookup(self, native_id: str) -> JobStatus:
        for result in self._rnk("job", "list"):
            if result["name"] == native_id:
                return self._status(result)
        raise SubmitException(f"Renku job {native_id!r} is unavailable")

    def _observe(self, job: Job) -> None:
        if not job.status.final:
            Thread(target=self._watch, args=(weakref.ref(job),), daemon=True).start()

    def _watch(self, reference: weakref.ReferenceType[Job]) -> None:
        while True:
            time.sleep(self.poll_interval)
            job = reference()
            if job is None or job.status.final:
                return
            try:
                self._set_job_status(job, self._lookup(job.native_id))
            except (SubmitException, KeyError, TypeError) as exc:
                logger.warning("Cannot observe Renku job %s: %s", job.native_id, exc)
            del job

    def cancel(self, job: Job) -> None:
        raise InvalidJobException("Use rnk job stop to cancel a Renku submission")


__PSI_J_EXECUTORS__ = [
    Descriptor(
        name="renku",
        version=Version("0.1.0"),
        cls="psij-descriptors.renku.RenkuJobExecutor",
    )
]
