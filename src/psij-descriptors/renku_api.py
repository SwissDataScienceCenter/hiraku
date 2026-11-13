"""PSI/J executor calling the Renku data services API with OAuth2 device login."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shlex
import sys
import time
import weakref
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from filelock import FileLock, Timeout
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

# Public Keycloak client with the device grant enabled on renkulab.io.
DEFAULT_CLIENT_ID = "renku-cli"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
HTTP_TIMEOUT = 20
# A login waits for the user to approve, so a second process may wait that long.
LOGIN_LOCK_TIMEOUT = 15 * 60
POLL_LOCK_TIMEOUT = 5
REPLACE_ATTEMPTS = 5


class _Auth:
    """Device-flow login with a per-deployment token cache and refresh."""

    def __init__(self, issuer: str, client_id: str) -> None:
        self._issuer = issuer.rstrip("/")
        self._client_id = client_id
        self._endpoints: dict[str, Any] | None = None
        self._tokens: dict[str, Any] | None = None

    @staticmethod
    def _cache_path() -> Path:
        base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
        return Path(base) / "hiraku" / "renku-api-tokens.json"

    def _load(self) -> dict[str, Any] | None:
        try:
            cache = json.loads(self._cache_path().read_text())
            return cache.get(self._issuer)
        except (OSError, ValueError, AttributeError):
            logger.warning("Failed to load cache.")
            return None

    def _save(self, tokens: dict[str, Any]) -> None:
        path = self._cache_path()
        try:
            cache = json.loads(path.read_text())
        except (OSError, ValueError):
            cache = {}
        cache[self._issuer] = tokens
        path.parent.mkdir(parents=True, exist_ok=True)
        # Tokens are credentials: create the file private, and replace it
        # atomically so concurrent readers never see a partial cache.
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(cache, handle)
        self._replace(temporary, path)

    @staticmethod
    def _replace(source: Path, target: Path) -> None:
        # Windows refuses to replace a file a reader has open; readers hold it
        # only briefly, so retry before giving up.
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                os.replace(source, target)
                return
            except PermissionError:
                if attempt == REPLACE_ATTEMPTS - 1:
                    source.unlink(missing_ok=True)
                    raise
                time.sleep(0.05)

    def _discover(self) -> dict[str, Any]:
        if self._endpoints is None:
            self._endpoints = _request_json(
                f"{self._issuer}/.well-known/openid-configuration"
            )
        return self._endpoints

    def _token_request(self, **fields: str) -> dict[str, Any]:
        endpoint = self._discover()["token_endpoint"]
        body = urlencode({"client_id": self._client_id, **fields}).encode()
        return _request_json(endpoint, data=body, accept_errors=True)

    def _store(self, response: dict[str, Any]) -> str:
        tokens = {
            "access_token": response["access_token"],
            "refresh_token": response.get("refresh_token"),
            "expires_at": time.time() + int(response.get("expires_in", 60)),
        }
        self._tokens = tokens
        self._save(tokens)
        return tokens["access_token"]

    def _refresh(self, tokens: dict[str, Any]) -> str | None:
        if not tokens.get("refresh_token"):
            return None
        response = self._token_request(
            grant_type="refresh_token", refresh_token=tokens["refresh_token"]
        )
        return self._store(response) if "access_token" in response else None

    def _login(self) -> str:
        endpoints = self._discover()
        device_endpoint = endpoints.get("device_authorization_endpoint")
        if not device_endpoint:
            raise SubmitException("The Renku identity provider has no device flow")
        device = _request_json(
            device_endpoint,
            data=urlencode({"client_id": self._client_id, "scope": "openid"}).encode(),
        )
        # stdout may carry job output, so prompt on stderr.
        print(
            "Authorize Renku access at "
            f"{device.get('verification_uri_complete') or device['verification_uri']}"
            f" (code: {device['user_code']})",
            file=sys.stderr,
        )
        interval = int(device.get("interval", 5))
        deadline = time.monotonic() + int(device.get("expires_in", 600))
        while time.monotonic() < deadline:
            time.sleep(interval)
            response = self._token_request(
                grant_type=DEVICE_GRANT, device_code=device["device_code"]
            )
            error = response.get("error")
            if error is None:
                return self._store(response)
            if error == "slow_down":
                interval += 5
            elif error != "authorization_pending":
                raise SubmitException(
                    f"Renku login failed: {response.get('error_description', error)}"
                )
        raise SubmitException("Renku login timed out")

    def token(self, *, interactive: bool) -> str:
        """Return a valid access token, logging in only when ``interactive``."""
        if self._fresh(self._tokens):
            return self._tokens["access_token"]
        # Cache writes are atomic, so a valid token needs no lock to read.
        tokens = self._load()
        if self._fresh(tokens):
            self._tokens = tokens
            return tokens["access_token"]
        # The file lock serializes refresh and login across threads and
        # processes: a reused refresh token can revoke the session, and two
        # logins would show two codes. Re-read the cache once it is held, since
        # another caller may have just renewed the token.
        path = self._cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(
            path.with_name(f"{path.name}.lock"),
            timeout=LOGIN_LOCK_TIMEOUT if interactive else POLL_LOCK_TIMEOUT,
        )
        try:
            with lock:
                tokens = self._load()
                if self._fresh(tokens):
                    self._tokens = tokens
                    return tokens["access_token"]
                if tokens:
                    refreshed = self._refresh(tokens)
                    if refreshed:
                        return refreshed
                if not interactive:
                    raise SubmitException("Renku login expired; submit a job to log in")
                return self._login()
        except Timeout as exc:
            raise SubmitException(
                "Another Hiraku process is refreshing the Renku login"
            ) from exc

    @staticmethod
    def _fresh(tokens: dict[str, Any] | None) -> bool:
        return bool(tokens) and tokens["expires_at"] - time.time() > 30


def _request_json(
    url: str,
    *,
    data: bytes | None = None,
    token: str | None = None,
    method: str | None = None,
    body: Any = None,
    accept_errors: bool = False,
) -> Any:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT) as response:  # noqa: S310
            payload = response.read()
    except HTTPError as exc:
        payload = exc.read()
        if not accept_errors:
            try:
                detail = json.loads(payload)["error"]["message"]
            except (ValueError, KeyError, TypeError):
                detail = payload.decode(errors="replace")[:200]
            raise SubmitException(f"Renku API {exc.code}: {detail}") from exc
    except (URLError, OSError) as exc:
        # A connect timeout arrives wrapped in URLError, a read timeout bare.
        if isinstance(exc, TimeoutError) or isinstance(
            getattr(exc, "reason", None), TimeoutError
        ):
            logger.warning("Renku request timed out after %ss: %s", HTTP_TIMEOUT, url)
            raise SubmitException(
                f"Renku did not answer within {HTTP_TIMEOUT}s: {url}"
            ) from exc
        raise SubmitException(f"Cannot reach Renku at {url}: {exc}") from exc
    return json.loads(payload) if payload else None


class RenkuAPIJobExecutor(JobExecutor):
    """Submit and observe Renku jobs through the data services API.

    The executor URL names the deployment and project. ``RENKU_CLIENT_ID`` and
    ``RENKU_OIDC_ISSUER`` override the device-flow client and identity provider.

    Invoked like:
    hiraku job submit --executor renku_api \
        --executor-url https://renkulab.io/p/namespace/project \
        --custom-attribute renku.launcher LAUNCHER_ID -- python train.py
    """

    poll_interval = 2.0

    def __init__(
        self,
        url: str | None = None,
        config: JobExecutorConfig | None = None,
    ) -> None:
        target = urlsplit(url or "")
        parts = target.path.removeprefix("/p/").strip("/").split("/")
        if (
            target.scheme not in ("http", "https")
            or not target.netloc
            or not target.path.startswith("/p/")
            or len(parts) != 2
            or not all(parts)
        ):
            raise ValueError("Use a Renku /p/namespace/project URL")
        self._namespace, self._slug = parts
        self._base_url = urlunsplit((target.scheme, target.netloc, "", "", ""))
        self._api_url = f"{self._base_url}/api/data"
        self._oidc_issuer = (
            os.environ.get("RENKU_OIDC_ISSUER") or f"{self._base_url}/auth/realms/Renku"
        )
        self._client_id = os.environ.get("RENKU_CLIENT_ID", DEFAULT_CLIENT_ID)
        self._auth = _Auth(self._oidc_issuer, self._client_id)
        self._project_id: str | None = None
        super().__init__(url, config or JobExecutorConfig())

    @staticmethod
    def _attributes(job: Job) -> dict[str, Any]:
        return (
            dict(job.spec.attributes.custom_attributes or {})
            if (job.spec and job.spec.attributes)
            else {}
        )

    def _call(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        interactive: bool = False,
    ) -> Any:
        return _request_json(
            f"{self._api_url}{path}",
            token=self._auth.token(interactive=interactive),
            method=method,
            body=body,
        )

    def _project_identifier(self) -> str:
        if self._project_id is None:
            namespace = quote(self._namespace, safe="")
            slug = quote(self._slug, safe="")
            project = self._call(
                "GET", f"/namespaces/{namespace}/projects/{slug}", interactive=True
            )
            self._project_id = project["id"]
        return self._project_id

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
        launcher_id = str(attrs["renku.launcher"])
        launcher = self._call(
            "GET", f"/session_launchers/{quote(launcher_id, safe='')}", interactive=True
        )
        if launcher["project_id"] != self._project_identifier():
            raise InvalidJobException("The launcher belongs to another Renku project")
        # The API accepts 4–20 lowercase alphanumeric/dash characters, not a UUID.
        submission_id = "h" + hashlib.sha256(job.id.encode()).hexdigest()[:19]
        result = self._call(
            "POST",
            "/sessions",
            {
                "launcher_id": launcher_id,
                "submission_id": submission_id,
                # Renku adds the buildpack launcher for code-built launchers itself.
                "job_command_override": argv[:1],
                "job_args_override": argv[1:],
            },
            interactive=True,
        )
        job._native_id = result["name"]
        self._set_job_status(job, self._status(result))
        self._observe(job)

    def list(self) -> list[str]:
        project_id = self._project_identifier()
        sessions = self._call("GET", "/sessions?session_type=non-interactive")
        return [item["name"] for item in sessions if item["project_id"] == project_id]

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
        # The API exposes final states, but not process exit codes.
        return JobStatus(state, message=status.get("message"))

    def _lookup(self, native_id: str) -> JobStatus:
        try:
            result = self._call("GET", f"/sessions/{quote(native_id, safe='')}")
        except SubmitException as exc:
            if "404" in str(exc):
                raise SubmitException(
                    f"Renku job {native_id!r} is unavailable"
                ) from exc
            raise
        return self._status(result)

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
        # Hibernating stops the job but keeps it (and its logs) until Renku culls
        # it; hibernating a finished job would turn its result into CANCELED.
        if job.status.final:
            return
        result = self._call(
            "PATCH",
            f"/sessions/{quote(job.native_id, safe='')}",
            {"state": "hibernated"},
        )
        self._set_job_status(job, self._status(result))


__PSI_J_EXECUTORS__ = [
    Descriptor(
        name="renku_api",
        version=Version("0.1.0"),
        cls="psij-descriptors.renku_api.RenkuAPIJobExecutor",
    )
]
