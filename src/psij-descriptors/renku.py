"""PSI/J executor using the authenticated rnk CLI, without a Renku SDK."""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
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


class RenkuJobExecutor(JobExecutor):
    """Submit through rnk; results and control remain with the remote service."""

    def __init__(
        self,
        url: str | None = None,
        config: JobExecutorConfig | None = None,
    ) -> None:
        target = urlsplit(url or "https://renkulab.io")
        self._project = target.path.removeprefix("/p/").strip("/") or None
        if target.path.strip("/") and not target.path.startswith("/p/"):
            raise ValueError("Use a Renku server URL or /p/namespace/project URL")
        self._server_url = urlunsplit((target.scheme, target.netloc, "", "", ""))
        super().__init__(url or "https://renkulab.io", config or JobExecutorConfig())

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
        command = ["rnk", "--renku-url", self._server_url]
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
        if (
            spec.pre_launch
            or spec.post_launch
            or spec.launcher not in (None, "single", "hiraku-single")
        ):
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

    def list(self) -> list[str]:
        return [item["name"] for item in self._rnk("job", "list")]

    def attach(self, job: Job, native_id: str) -> None:
        raise InvalidJobException("Inspect Renku submissions through rnk")

    def cancel(self, job: Job) -> None:
        raise InvalidJobException("Use rnk job stop to cancel a Renku submission")


__PSI_J_EXECUTORS__ = [
    Descriptor(
        name="renku",
        version=Version("0.1.0"),
        cls="psij-descriptors.renku.RenkuJobExecutor",
    )
]
