from datetime import timedelta
from pathlib import Path

import psij
import pytest

URL = "https://renkulab.io/p/namespace/project"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for name in ("RENKU_CLIENT_ID", "RENKU_OIDC_ISSUER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


@pytest.fixture
def renku(monkeypatch):
    executor = psij.JobExecutor.get_instance("renku", url=URL)
    assert executor.name == "renku"
    monkeypatch.setattr(executor, "poll_interval", 0.01)
    return executor


def test_executor_url_is_parsed_once_in_init():
    executor = psij.JobExecutor.get_instance(
        "renku", url="https://dev.renku.ch/p/namespace/project"
    )
    assert (executor._namespace, executor._slug) == ("namespace", "project")
    assert executor._base_url == "https://dev.renku.ch"
    assert executor._api_url == "https://dev.renku.ch/api/data"
    assert executor._oidc_issuer == "https://dev.renku.ch/auth/realms/Renku"
    assert executor._client_id == "renku-cli"


def test_oidc_settings_come_from_env(monkeypatch):
    monkeypatch.setenv("RENKU_CLIENT_ID", "mine")
    monkeypatch.setenv("RENKU_OIDC_ISSUER", "https://sso.example/realms/r")
    executor = psij.JobExecutor.get_instance("renku", url=URL)
    assert executor._client_id == "mine"
    assert executor._oidc_issuer == "https://sso.example/realms/r"
    assert executor._auth._client_id == "mine"


@pytest.mark.parametrize(
    "url", [None, "/p/namespace/project", "/p/onlyone", "https://renkulab.io/x/a/b"]
)
def test_rejects_bad_project_url(url):
    with pytest.raises(ValueError, match="/p/namespace/project"):
        psij.JobExecutor.get_instance("renku", url=url)


def test_submission_and_cancel(renku):
    calls = []

    def call(method, path, body=None, *, interactive=False):
        calls.append((method, path, body))
        if path.startswith("/namespaces/"):
            return {"id": "P1"}
        if path.startswith("/session_launchers/"):
            return {"project_id": "P1"}
        if method == "POST":
            return {"name": "remote-1", "status": {"state": "running"}}
        if method == "PATCH":
            return {"name": "remote-1", "status": {"state": "hibernated"}}
        return None

    renku._call = call
    job = psij.Job(
        psij.JobSpec(
            executable="python",
            arguments=["train.py"],
            attributes=psij.JobAttributes(custom_attributes={"renku.launcher": "L1"}),
        )
    )
    renku.submit(job)
    assert job.native_id == "remote-1"
    assert job.status.state == psij.JobState.ACTIVE
    post = next(c for c in calls if c[0] == "POST")
    assert post[2]["launcher_id"] == "L1"
    # The backend wraps commands for code-built launchers.
    assert post[2]["job_command_override"] == ["env"]
    assert post[2]["job_args_override"] == ["python", "train.py"]
    renku.cancel(job)
    assert calls[-1] == ("PATCH", "/sessions/remote-1", {"state": "hibernated"})
    assert job.status.state == psij.JobState.CANCELED
    assert not any(call[0] == "DELETE" for call in calls)
    count = len(calls)
    renku.cancel(job)
    assert len(calls) == count  # final jobs are left alone


def test_launcher_from_another_project_is_rejected(renku):
    renku._call = lambda method, path, *a, **k: (
        {"id": "P1"} if path.startswith("/namespaces/") else {"project_id": "P2"}
    )
    job = psij.Job(
        psij.JobSpec(
            executable="true",
            attributes=psij.JobAttributes(custom_attributes={"renku.launcher": "L"}),
        )
    )
    with pytest.raises(psij.InvalidJobException, match="another Renku project"):
        renku.submit(job)


def test_status_and_missing_job(renku):
    status = renku._status({"status": {"state": "starting"}})
    assert status.state == psij.JobState.QUEUED
    with pytest.raises(psij.SubmitException, match="Unknown Renku state"):
        renku._status({"status": {"state": "weird"}})

    def missing(*args, **kwargs):
        raise psij.SubmitException("Renku API 404: nope")

    renku._call = missing
    with pytest.raises(psij.SubmitException, match="unavailable"):
        renku.attach(psij.Job(), "gone")


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("succeeded", psij.JobState.COMPLETED),
        ("failed", psij.JobState.FAILED),
        ("hibernated", psij.JobState.CANCELED),
    ],
)
def test_submission_and_final_status(renku, monkeypatch, state, expected):
    calls = []

    def call(method, path, body=None, **kwargs):
        calls.append((method, path, body))
        if path.startswith("/namespaces/"):
            return {"id": "P1"}
        if path.startswith("/session_launchers/"):
            return {"project_id": "P1"}
        if method == "POST":
            return {"name": "remote-1", "status": {"state": "starting"}}
        result = {
            "name": "remote-1",
            "project_id": "P1",
            "status": {"state": state, "message": "remote"},
        }
        if path.startswith("/sessions?"):
            return [result, {"name": "other", "project_id": "P2"}]
        return result

    monkeypatch.setattr(renku, "_call", call)
    job = psij.Job(
        psij.JobSpec(
            "python",
            ["train.py", "argument with spaces"],
            directory=Path("remote work"),
            environment={"CUSTOM": "value with spaces"},
            attributes=psij.JobAttributes(custom_attributes={"renku.launcher": "L1"}),
        )
    )
    renku.submit(job)
    status = job.wait(timeout=timedelta(seconds=5))
    assert status.state == expected
    assert status.exit_code is None
    assert status.message == "remote"
    body = next(body for method, _, body in calls if method == "POST")
    assert body["job_command_override"] == ["sh"]
    assert (
        "cd 'remote work' && exec env 'CUSTOM=value with spaces'"
        in body["job_args_override"][-1]
    )
    attached = psij.Job()
    renku.attach(attached, "remote-1")
    assert attached.status.state == expected
    assert renku.list() == ["remote-1"]


def test_observation_error_does_not_invent_failure(renku, monkeypatch):
    attempts = 0

    def call(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise psij.SubmitException("temporary outage")
        state = "running" if attempts == 1 else "succeeded"
        return {"name": "remote", "status": {"state": state}}

    monkeypatch.setattr(renku, "_call", call)
    job = psij.Job()
    renku.attach(job, "remote")
    assert job.wait(timeout=timedelta(seconds=5)).state == psij.JobState.COMPLETED
    assert attempts >= 3


def test_device_flow_login_and_cache(monkeypatch):
    from importlib import util
    from pathlib import Path

    path = Path(__file__).parents[1] / "src/psij-descriptors/renku.py"
    spec = util.spec_from_file_location("renku_under_test", path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)

    responses = iter(
        [
            {"token_endpoint": "t", "device_authorization_endpoint": "d"},
            {
                "device_code": "dc",
                "user_code": "AB-CD",
                "verification_uri": "v",
                "interval": 0,
            },
            {"error": "authorization_pending"},
            {"access_token": "tok", "refresh_token": "r", "expires_in": 300},
        ]
    )
    monkeypatch.setattr(module, "_request_json", lambda *a, **k: next(responses))
    monkeypatch.setattr(module.time, "sleep", lambda s: None)
    auth = module._Auth("https://x/auth/realms/Renku", "renku-cli")
    with pytest.raises(psij.SubmitException, match="submit a job to log in"):
        auth.token(interactive=False)
    assert auth.token(interactive=True) == "tok"

    # A fresh instance reuses the cached token without any request.
    monkeypatch.setattr(module, "_request_json", lambda *a, **k: 1 / 0)
    again = module._Auth("https://x/auth/realms/Renku", "renku-cli")
    assert again.token(interactive=False) == "tok"


def _load_module():
    from importlib import util
    from pathlib import Path

    path = Path(__file__).parents[1] / "src/psij-descriptors/renku.py"
    spec = util.spec_from_file_location("renku_under_test", path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_waits_for_other_process_and_reuses_its_token(monkeypatch):
    import json
    import threading
    import time

    from filelock import FileLock

    module = _load_module()
    issuer = "https://x/auth/realms/Renku"
    auth = module._Auth(issuer, "renku-cli")
    path = auth._cache_path()
    path.parent.mkdir(parents=True)
    held = threading.Event()

    def other_process():
        with FileLock(path.with_name(f"{path.name}.lock")):
            held.set()
            time.sleep(0.2)
            tokens = {"access_token": "theirs", "expires_at": time.time() + 300}
            path.write_text(json.dumps({issuer: tokens}))

    thread = threading.Thread(target=other_process)
    thread.start()
    held.wait()
    monkeypatch.setattr(module, "_request_json", lambda *a, **k: 1 / 0)
    assert auth.token(interactive=True) == "theirs"
    thread.join()


def test_poller_does_not_wait_for_a_login_in_progress(monkeypatch):
    from filelock import FileLock

    module = _load_module()
    monkeypatch.setattr(module, "POLL_LOCK_TIMEOUT", 0.05)
    auth = module._Auth("https://x/auth/realms/Renku", "renku-cli")
    path = auth._cache_path()
    path.parent.mkdir(parents=True)
    with (
        FileLock(path.with_name(f"{path.name}.lock")),
        pytest.raises(psij.SubmitException, match="Another Hiraku process"),
    ):
        auth.token(interactive=False)


def test_valid_cached_token_is_used_while_another_login_holds_the_lock():
    import json
    import time

    from filelock import FileLock

    module = _load_module()
    issuer = "https://x/auth/realms/Renku"
    auth = module._Auth(issuer, "renku-cli")
    path = auth._cache_path()
    path.parent.mkdir(parents=True)
    tokens = {"access_token": "cached", "expires_at": time.time() + 300}
    path.write_text(json.dumps({issuer: tokens}))
    with FileLock(path.with_name(f"{path.name}.lock")):
        assert auth.token(interactive=False) == "cached"


def test_cache_replace_retries_when_a_reader_holds_the_file(monkeypatch):
    import json
    import os

    module = _load_module()
    monkeypatch.setattr(module.time, "sleep", lambda s: None)
    real_replace = os.replace
    failures = iter([PermissionError(), PermissionError()])

    def flaky(source, target):
        for error in failures:
            raise error
        real_replace(source, target)

    monkeypatch.setattr(module.os, "replace", flaky)
    auth = module._Auth("https://x/auth/realms/Renku", "renku-cli")
    auth._save({"access_token": "tok"})
    saved = json.loads(auth._cache_path().read_text())
    assert saved["https://x/auth/realms/Renku"]["access_token"] == "tok"


def test_cache_replace_gives_up_and_removes_the_temporary_file(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(module.time, "sleep", lambda s: None)

    def denied(source, target):
        raise PermissionError

    monkeypatch.setattr(module.os, "replace", denied)
    auth = module._Auth("https://x/auth/realms/Renku", "renku-cli")
    with pytest.raises(PermissionError):
        auth._save({"access_token": "tok"})
    assert list(auth._cache_path().parent.glob("*.tmp")) == []


@pytest.mark.parametrize(
    "error", [TimeoutError(), __import__("urllib.error").error.URLError(TimeoutError())]
)
def test_request_timeout_warns_and_raises(monkeypatch, caplog, error):
    module = _load_module()

    def slow(*args, **kwargs):
        raise error

    monkeypatch.setattr(module, "urlopen", slow)
    with (
        caplog.at_level("WARNING"),
        pytest.raises(psij.SubmitException, match="did not answer"),
    ):
        module._request_json("https://x/api/data/sessions")
    assert "timed out" in caplog.text


def test_unreachable_host_is_not_reported_as_a_timeout(monkeypatch, caplog):
    from urllib.error import URLError

    module = _load_module()

    def refused(*args, **kwargs):
        raise URLError(ConnectionRefusedError())

    monkeypatch.setattr(module, "urlopen", refused)
    with pytest.raises(psij.SubmitException, match="Cannot reach"):
        module._request_json("https://x/api/data/sessions")
    assert "timed out" not in caplog.text


def test_renku_rejects_unsupported_specs(renku):
    with pytest.raises(psij.InvalidJobException, match="renku.launcher"):
        renku.submit(psij.Job(psij.JobSpec("echo")))
    for field, value in [
        ("resources", psij.ResourceSpecV1(process_count=2)),
        ("pre_launch", Path("hook.sh")),
        ("launcher", "mpi"),
        ("stdout_path", Path("output.log")),
    ]:
        spec = psij.JobSpec(
            "echo",
            attributes=psij.JobAttributes(
                custom_attributes={"renku.launcher": "launcher"}
            ),
        )
        setattr(spec, field, value)
        with pytest.raises(psij.InvalidJobException):
            renku.submit(psij.Job(spec))
