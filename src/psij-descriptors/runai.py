"""PSI/J executor for single-container RunAI training workloads over HTTPS."""

from __future__ import annotations

import json
import logging
import os
import shlex
import time
import weakref
from pathlib import Path
from threading import Lock, Thread
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

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
HTTP_TIMEOUT = 20


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward bearer credentials to a redirected host.
        raise SubmitException("RunAI API redirects are not supported")


_opener = build_opener(_NoRedirect())


class _APIError(SubmitException):
    def __init__(self, code: int, path: str) -> None:
        self.code = code
        # Response bodies may echo credentials or environment values.
        super().__init__(f"RunAI API HTTP {code} at {path}")
        self.args = (self.message,)


def _request_json(
    url: str, *, method: str = "GET", token: str | None = None, body: Any = None
) -> Any:
    headers = {"Accept": "application/json", "User-Agent": "hiraku-runai/0.1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with _opener.open(request, timeout=HTTP_TIMEOUT) as response:
            payload = response.read()
    except HTTPError as exc:
        raise _APIError(exc.code, urlsplit(url).path) from None
    except (URLError, OSError):
        raise SubmitException(
            f"Cannot reach RunAI at {urlsplit(url).path} (timeout {HTTP_TIMEOUT}s)"
        ) from None
    try:
        return json.loads(payload) if payload else None
    except ValueError:
        raise SubmitException("RunAI returned invalid JSON") from None


class _Auth:
    """Exchange access-key credentials; keep short-lived tokens only in memory."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = Lock()

    def _credentials(self) -> dict[str, str]:
        client_id = os.environ.get("RUNAI_CLIENT_ID")
        secret = os.environ.get("RUNAI_CLIENT_SECRET")
        if client_id or secret:
            if not client_id or not secret:
                raise SubmitException(
                    "Set both RUNAI_CLIENT_ID and RUNAI_CLIENT_SECRET"
                )
            return {"clientId": client_id, "clientSecret": secret}
        root = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
        path = Path(root) / "hiraku" / "runai-credentials.json"
        try:
            if os.name == "posix" and path.stat().st_mode & 0o077:
                raise SubmitException(f"RunAI credential file must be private: {path}")
            entry = json.loads(path.read_text())[self.base_url]
            if not all(
                isinstance(entry[k], str) and entry[k]
                for k in ("client_id", "client_secret")
            ):
                raise ValueError
            return {
                "clientId": entry["client_id"],
                "clientSecret": entry["client_secret"],
            }
        except (OSError, ValueError, KeyError, TypeError):
            raise SubmitException(
                "Set RUNAI_CLIENT_ID and RUNAI_CLIENT_SECRET or configure "
                f"{path} for {self.base_url}"
            ) from None

    def token(self, *, renew: bool = False) -> str:
        with self._lock:
            if not renew and self._token and time.monotonic() < self._expires_at:
                return self._token
            response = _request_json(
                f"{self.base_url}/api/v1/token",
                method="POST",
                body={"grantType": "client_credentials", **self._credentials()},
            )
            try:
                token = response["accessToken"]
                lifetime = float(response.get("expiresIn", 60))
                if not isinstance(token, str) or not token or lifetime <= 0:
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                raise SubmitException("RunAI token response is invalid") from None
            self._token = token
            self._expires_at = time.monotonic() + max(0, lifetime - 30)
            return token


class RunAIJobExecutor(JobExecutor):
    """API-only training executor; URL query selects cluster and project names/IDs."""

    poll_interval = 5.0

    def __init__(
        self, url: str | None = None, config: JobExecutorConfig | None = None
    ) -> None:
        target = urlsplit(url or "")
        query = parse_qs(target.query, keep_blank_values=True)
        if (
            target.scheme != "https"
            or not target.netloc
            or target.username is not None
            or target.password is not None
            or target.path not in ("", "/")
            or target.fragment
            or set(query) != {"cluster", "project"}
            or any(len(v) != 1 or not v[0].strip() for v in query.values())
        ):
            raise ValueError(
                "Use https://HOST?cluster=CLUSTER_NAME_OR_ID&project=PROJECT_NAME_OR_ID"
            )
        self._base_url = urlunsplit((target.scheme, target.netloc, "", "", ""))
        self._cluster = query["cluster"][0]
        self._project = query["project"][0]
        self._target_ids: tuple[str, str] | None = None
        self._target_lock = Lock()
        self._auth = _Auth(self._base_url)
        super().__init__(url, config or JobExecutorConfig())

    def _call(self, method: str, path: str, body: Any = None) -> Any:
        token = self._auth.token()
        try:
            return _request_json(
                self._base_url + path, method=method, token=token, body=body
            )
        except _APIError as exc:
            if exc.code != 401:
                raise
        # Only retry an explicit authentication rejection, never a timeout/5xx POST.
        return _request_json(
            self._base_url + path,
            method=method,
            token=self._auth.token(renew=True),
            body=body,
        )

    def _pages(self, path: str, key: str, filters: list[str] | None = None) -> list:
        items, offset = [], 0
        while True:
            query = [("filterBy", f) for f in (filters or [])]
            query += [("limit", 100), ("offset", offset)]
            result = self._call("GET", path + "?" + urlencode(query))
            items.extend(result[key])
            following = result.get("next")
            if following is None:
                return items
            if type(following) is not int or following <= offset:
                raise SubmitException("RunAI returned an invalid pagination cursor")
            offset = following

    def _target(self) -> tuple[str, str]:
        with self._target_lock:
            if self._target_ids is None:
                clusters = [
                    c
                    for c in self._call("GET", "/api/v1/clusters")
                    if self._cluster in (str(c["uuid"]), c["name"])
                ]
                if len(clusters) != 1:
                    raise SubmitException(
                        "RunAI cluster name/ID must match one cluster"
                    )
                cluster_id = str(clusters[0]["uuid"])
                projects = [
                    p
                    for p in self._pages(
                        "/api/v1/org-unit/projects",
                        "projects",
                        [f"clusterId=={cluster_id}"],
                    )
                    if str(p["clusterId"]) == cluster_id
                    and self._project in (str(p["id"]), p["name"])
                ]
                if len(projects) != 1:
                    raise SubmitException(
                        "RunAI project name/ID must match one project"
                    )
                self._target_ids = cluster_id, str(projects[0]["id"])
            return self._target_ids

    def submit(self, job: Job) -> None:
        if job.spec and job.spec.resources is not None:
            raise InvalidJobException("Use runai.compute for RunAI resources")
        spec = self._check_job(job)
        if (
            spec.pre_launch
            or spec.post_launch
            or spec.launcher not in (None, "single")
            or spec.stdin_path
            or spec.stdout_path
            or spec.stderr_path
        ):
            raise InvalidJobException(
                "RunAI does not support PSI/J hooks or stream paths"
            )
        attrs = dict(spec.attributes.custom_attributes or {}) if spec.attributes else {}
        supported = {"image", "compute", "storage", "node_type", "security"}
        if any(k not in {f"runai.{s}" for s in supported} for k in attrs):
            raise InvalidJobException("Unknown RunAI custom attribute")
        image = attrs.get("runai.image")
        if not isinstance(image, str) or not image.strip():
            raise InvalidJobException("Set runai.image to the container image")
        # The API represents command/args as strings; POSIX quoting preserves argv.
        container = {
            "image": image,
            "command": shlex.quote(spec.executable),
            "args": shlex.join(spec.arguments or []),
            "environmentVariables": [
                {"name": k, "value": str(v)}
                for k, v in (spec.environment or {}).items()
            ],
            "restartPolicy": "Never",
            "backoffLimit": 0,
            "completions": 1,
            "parallelism": 1,
        }
        if spec.directory is not None:
            if not spec.directory.is_absolute():
                raise InvalidJobException("RunAI working directory must be absolute")
            container["workingDir"] = str(spec.directory)
        for name in ("compute", "storage", "security"):
            if f"runai.{name}" in attrs:
                value = attrs[f"runai.{name}"]
                try:
                    value = json.loads(value) if isinstance(value, str) else value
                    if not isinstance(value, dict):
                        raise ValueError
                except ValueError:
                    raise InvalidJobException(
                        f"runai.{name} must be a JSON object"
                    ) from None
                container[name] = value
        if "runai.node_type" in attrs:
            if not isinstance(attrs["runai.node_type"], str):
                raise InvalidJobException("runai.node_type must be a string")
            container["nodeType"] = attrs["runai.node_type"]
        cluster_id, project_id = self._target()
        name = "hiraku-" + job.id
        try:
            result = self._call(
                "POST",
                "/api/v1/workloads/trainings",
                {
                    "name": name,
                    "clusterId": cluster_id,
                    "projectId": project_id,
                    "spec": container,
                },
            )
        except SubmitException as exc:
            raise SubmitException(
                f"{exc}; check RunAI workload {name!r} before resubmitting"
            ) from None
        job._native_id = result["workloadId"]
        self._set_job_status(
            job,
            self._status(
                {**result, "actualPhase": result.get("actualPhase") or "Creating"}
            ),
        )
        self._observe(job)

    def list(self) -> list[str]:
        cluster_id, project_id = self._target()
        items = self._pages(
            "/api/v1/workloads",
            "workloads",
            [
                f"clusterId=={cluster_id}",
                f"projectId=={project_id}",
            ],
        )
        return [
            str(w["id"])
            for w in items
            if str(w["projectId"]) == project_id
            and str(w["clusterId"]) == cluster_id
            and w["type"].lower() == "training"
        ]

    @staticmethod
    def _status(result: dict[str, Any]) -> JobStatus:
        phase = result.get("actualPhase")
        states = {
            "Creating": JobState.QUEUED,
            "Pending": JobState.QUEUED,
            "Initializing": JobState.QUEUED,
            "Resuming": JobState.QUEUED,
            "Running": JobState.ACTIVE,
            "Degraded": JobState.ACTIVE,
            "Suspending": JobState.ACTIVE,
            "Deleting": JobState.ACTIVE,
            "Stopped": JobState.CANCELED,
            "Deleted": JobState.CANCELED,
            "Failed": JobState.FAILED,
            "Completed": JobState.COMPLETED,
        }
        if phase not in states:
            raise SubmitException(f"Unknown RunAI phase: {phase!r}")
        # Training phase is authoritative; do not invent process exit codes.
        return JobStatus(states[phase], metadata={"runai_phase": phase})

    def _lookup(self, native_id: str) -> JobStatus:
        result = self._call(
            "GET", f"/api/v1/workloads/trainings/{quote(native_id, safe='')}"
        )
        cluster_id, project_id = self._target()
        if (
            str(result["projectId"]) != project_id
            or str(result["clusterId"]) != cluster_id
        ):
            raise SubmitException("RunAI workload is outside the configured target")
        return self._status(result)

    def attach(self, job: Job, native_id: str) -> None:
        if job.status.state != JobState.NEW or job.executor is not None:
            raise InvalidJobException("Job must be new and unbound")
        status = self._lookup(native_id)
        job.executor = self
        job._native_id = native_id
        self._set_job_status(job, status)
        self._observe(job)

    def cancel(self, job: Job) -> None:
        if job.status.final:
            return
        status = self._lookup(job.native_id)
        self._set_job_status(job, status)
        if status.final:
            return
        self._call(
            "POST",
            f"/api/v1/workloads/trainings/{quote(job.native_id, safe='')}/suspend",
        )
        # Accepted suspension is not confirmation that the container has stopped.
        self._set_job_status(job, self._lookup(job.native_id))

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
                logger.warning("Cannot observe RunAI job %s: %s", job.native_id, exc)
            del job


__PSI_J_EXECUTORS__ = [
    Descriptor(
        name="runai",
        version=Version("0.1.0"),
        cls="psij-descriptors.runai.RunAIJobExecutor",
    )
]
