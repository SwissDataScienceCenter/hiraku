import io
import json
from datetime import timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import psij
import pytest

URL = "https://runai.example?cluster=compute&project=research"


def module():
    import importlib

    return importlib.import_module("psij-descriptors.runai")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for key in ("RUNAI_CLIENT_ID", "RUNAI_CLIENT_SECRET"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


@pytest.fixture
def executor(monkeypatch):
    ex = psij.JobExecutor.get_instance("runai", url=URL)
    monkeypatch.setattr(ex, "_observe", lambda job: None)
    return ex


def job(**kwargs):
    return psij.Job(
        psij.JobSpec(
            executable="python",
            arguments=["train.py", "argument with spaces", "$(false)"],
            attributes=psij.JobAttributes(
                custom_attributes={
                    "runai.image": "registry.example/image:tag",
                    "runai.compute": '{"gpuDevicesRequest":1,"cpuMemoryRequest":"32G"}',
                    "runai.storage": '{"pvc":[{"claimName":"data","path":"/data"}]}',
                }
            ),
            **kwargs,
        )
    )


def remote(phase="Running", **kwargs):
    return {
        "workloadId": "W1",
        "projectId": "P1",
        "clusterId": "C1",
        "actualPhase": phase,
        **kwargs,
    }


def backend(executor, monkeypatch, phase="Running"):
    calls = []

    def call(method, path, body=None):
        calls.append((method, path, body))
        if path == "/api/v1/clusters":
            return [{"uuid": "C1", "name": "compute"}]
        if path.startswith("/api/v1/org-unit/projects?"):
            return {"projects": [{"id": "P1", "name": "research", "clusterId": "C1"}]}
        return remote(phase)

    monkeypatch.setattr(executor, "_call", call)
    return calls


@pytest.mark.parametrize(
    "url",
    [
        None,
        "http://host?cluster=c&project=p",
        "https://host",
        "https://host/path?cluster=c&project=p",
        "https://user:secret@host?cluster=c&project=p",
        "https://host?cluster=c&project=",
        "https://host?cluster=c&project=p&project=q",
        "https://host?cluster=c&project=p&token=x",
        "https://host?cluster=c&project=p#fragment",
    ],
)
def test_invalid_target(url):
    with pytest.raises(ValueError, match="https://HOST"):
        psij.JobExecutor.get_instance("runai", url=url)


def test_submit_preserves_argv_resources_and_remote_directory(executor, monkeypatch):
    calls = backend(executor, monkeypatch)
    native = job(directory=Path("/remote work"), environment={"NAME": "a b", "N": 2})
    executor.submit(native)
    assert native.executor is executor and native.native_id == "W1"
    assert native.status.state == psij.JobState.ACTIVE
    method, path, body = calls[-1]
    assert (method, path) == ("POST", "/api/v1/workloads/trainings")
    assert body["name"] == f"hiraku-{native.id}"
    assert (body["clusterId"], body["projectId"]) == ("C1", "P1")
    spec = body["spec"]
    import shlex

    assert shlex.split(spec["command"]) == ["python"]
    assert shlex.split(spec["args"]) == native.spec.arguments
    assert spec["workingDir"] == "/remote work"
    assert spec["compute"]["gpuDevicesRequest"] == 1
    assert spec["storage"]["pvc"][0]["claimName"] == "data"
    assert spec["environmentVariables"] == [
        {"name": "NAME", "value": "a b"},
        {"name": "N", "value": "2"},
    ]
    assert spec["restartPolicy"] == "Never" and spec["backoffLimit"] == 0


@pytest.mark.parametrize(
    "change,match",
    [
        ({"resources": psij.ResourceSpecV1(process_count=2)}, "runai.compute"),
        ({"directory": Path("relative")}, "absolute"),
        ({"launcher": "multiple"}, "hooks"),
        ({"pre_launch": Path("before")}, "hooks"),
        ({"post_launch": Path("after")}, "hooks"),
        ({"stdout_path": Path("out")}, "stream"),
        ({"stderr_path": Path("err")}, "stream"),
        ({"stdin_path": Path("in")}, "stream"),
    ],
)
def test_reject_unsupported_spec(executor, monkeypatch, change, match):
    monkeypatch.setattr(executor, "_call", lambda *a: pytest.fail("No network"))
    with pytest.raises(psij.InvalidJobException, match=match):
        executor.submit(job(**change))


@pytest.mark.parametrize(
    "attributes",
    [
        {},
        {"runai.image": ""},
        {"runai.image": "image", "runai.compute": "[]"},
        {"runai.image": "image", "runai.storage": "bad JSON"},
        {"runai.image": "image", "runai.client_secret": "private"},
    ],
)
def test_reject_bad_attributes(executor, monkeypatch, attributes):
    monkeypatch.setattr(executor, "_call", lambda *a: pytest.fail("No network"))
    native = job()
    native.spec.attributes.custom_attributes = attributes
    with pytest.raises(psij.InvalidJobException):
        executor.submit(native)


@pytest.mark.parametrize(
    "phase,state",
    [
        ("Creating", "QUEUED"),
        ("Pending", "QUEUED"),
        ("Initializing", "QUEUED"),
        ("Resuming", "QUEUED"),
        ("Running", "ACTIVE"),
        ("Degraded", "ACTIVE"),
        ("Suspending", "ACTIVE"),
        ("Deleting", "ACTIVE"),
        ("Stopped", "CANCELED"),
        ("Deleted", "CANCELED"),
        ("Completed", "COMPLETED"),
        ("Failed", "FAILED"),
    ],
)
def test_status(executor, phase, state):
    status = executor._status(remote(phase))
    assert status.state.name == state
    assert status.exit_code is None
    assert status.metadata == {"runai_phase": phase}


def test_unknown_status(executor):
    with pytest.raises(psij.SubmitException, match="Unknown RunAI phase"):
        executor._status(remote("Mystery"))


@pytest.mark.parametrize("other", [{"projectId": "P2"}, {"clusterId": "C2"}])
@pytest.mark.parametrize("operation", ["attach", "cancel"])
def test_target_isolation(executor, monkeypatch, other, operation):
    executor._target_ids = "C1", "P1"
    monkeypatch.setattr(executor, "_call", lambda *a: remote(**other))
    native = psij.Job()
    with pytest.raises(psij.SubmitException, match="outside"):
        if operation == "attach":
            executor.attach(native, "W1")
        else:
            native.executor, native._native_id = executor, "W1"
            executor.cancel(native)
    if operation == "attach":
        assert native.executor is None and native.native_id is None


def test_attach_final_and_cancel_final(executor, monkeypatch):
    calls = backend(executor, monkeypatch, "Completed")
    native = psij.Job()
    executor.attach(native, "W1")
    assert native.wait(timeout=timedelta(seconds=1)).state == psij.JobState.COMPLETED
    n = len(calls)
    executor.cancel(native)
    assert len(calls) == n


def test_cancel_waits_for_stopped_confirmation(executor, monkeypatch):
    executor._target_ids = "C1", "P1"
    states = iter(["Running", "Suspending", "Stopped"])
    calls = []

    def call(method, path, body=None):
        calls.append((method, path))
        return remote(next(states)) if method == "GET" else {"code": 202}

    monkeypatch.setattr(executor, "_call", call)
    native = psij.Job()
    native.executor, native._native_id = executor, "W1"
    executor.cancel(native)
    assert native.status.state == psij.JobState.ACTIVE
    assert calls[1] == ("POST", "/api/v1/workloads/trainings/W1/suspend")
    executor._set_job_status(native, executor._lookup("W1"))
    assert native.status.state == psij.JobState.CANCELED


def test_cancel_does_not_suspend_remotely_completed_job(executor, monkeypatch):
    calls = backend(executor, monkeypatch, "Completed")
    native = psij.Job()
    native.executor, native._native_id = executor, "W1"
    executor.cancel(native)
    assert native.status.state == psij.JobState.COMPLETED
    assert all(method == "GET" for method, _, _ in calls)


def test_list_paginates_and_filters(executor, monkeypatch):
    executor._target_ids = "C1", "P1"
    queries = []

    def call(method, path):
        queries.append(parse_qs(urlsplit(path).query))
        if len(queries) == 1:
            return {
                "workloads": [
                    {
                        "id": "W1",
                        "type": "training",
                        "projectId": "P1",
                        "clusterId": "C1",
                    },
                    {
                        "id": "W2",
                        "type": "workspace",
                        "projectId": "P1",
                        "clusterId": "C1",
                    },
                    {
                        "id": "W3",
                        "type": "training",
                        "projectId": "P2",
                        "clusterId": "C1",
                    },
                ],
                "next": 3,
            }
        return {
            "workloads": [
                {"id": "W4", "type": "training", "projectId": "P1", "clusterId": "C1"},
            ]
        }

    monkeypatch.setattr(executor, "_call", call)
    assert executor.list() == ["W1", "W4"]
    assert queries[0]["filterBy"] == ["clusterId==C1", "projectId==P1"]
    assert queries[1]["offset"] == ["3"]


def test_observation_failure_does_not_invent_final_state(executor, monkeypatch):
    executor._target_ids = "C1", "P1"
    monkeypatch.setattr(executor, "poll_interval", 0.01)
    # Re-enable the real observer for this test only.
    monkeypatch.setattr(executor, "_observe", type(executor)._observe.__get__(executor))
    attempts = 0

    def call(*args):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise psij.SubmitException("temporary outage")
        return remote("Running" if attempts == 1 else "Completed")

    monkeypatch.setattr(executor, "_call", call)
    native = psij.Job()
    executor.attach(native, "W1")
    assert native.wait(timeout=timedelta(seconds=5)).state == psij.JobState.COMPLETED
    assert attempts >= 3


def test_auth_reuses_and_renews_tokens(monkeypatch):
    mod = module()
    monkeypatch.setenv("RUNAI_CLIENT_ID", "client")
    monkeypatch.setenv("RUNAI_CLIENT_SECRET", "secret")
    calls = []
    clock = [0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock[0])

    def request(url, **kwargs):
        calls.append((url, kwargs))
        return {"accessToken": f"token-{len(calls)}", "expiresIn": 300}

    monkeypatch.setattr(mod, "_request_json", request)
    auth = mod._Auth("https://runai.example")
    assert auth.token() == auth.token() == "token-1"
    clock[0] = 300
    assert auth.token() == "token-2"
    assert calls[0][1]["body"] == {
        "grantType": "client_credentials",
        "clientId": "client",
        "clientSecret": "secret",
    }


def test_credentials_file_is_deployment_scoped_and_private(tmp_path):
    path = tmp_path / "hiraku" / "runai-credentials.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "https://runai.example": {
                    "client_id": "client",
                    "client_secret": "secret",
                }
            }
        )
    )
    path.chmod(0o600)
    assert (
        module()._Auth("https://runai.example")._credentials()["clientId"] == "client"
    )
    with pytest.raises(psij.SubmitException, match="configure"):
        module()._Auth("https://other.example")._credentials()
    path.chmod(0o644)
    with pytest.raises(psij.SubmitException, match="private"):
        module()._Auth("https://runai.example")._credentials()


def test_partial_env_credentials_fail_instead_of_mixing_sources(monkeypatch):
    monkeypatch.setenv("RUNAI_CLIENT_ID", "client")
    with pytest.raises(psij.SubmitException, match="both"):
        module()._Auth("https://runai.example")._credentials()


def test_401_renews_once_but_403_does_not(executor, monkeypatch):
    # PSI/J loads descriptors without registering the module in sys.modules.
    namespace = type(executor)._call.__globals__
    error_type = namespace["_APIError"]
    tokens, calls = [], []

    def token(**kwargs):
        tokens.append(kwargs)
        return "token"

    def request(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise error_type(401, "/api/v1/workloads")
        return {"workloads": []}

    monkeypatch.setattr(executor._auth, "token", token)
    monkeypatch.setitem(namespace, "_request_json", request)
    assert executor._call("GET", "/api/v1/workloads") == {"workloads": []}
    assert tokens == [{}, {"renew": True}]
    monkeypatch.setitem(
        namespace,
        "_request_json",
        lambda *a, **k: (_ for _ in ()).throw(error_type(403, "/api/v1/workloads")),
    )
    with pytest.raises(psij.SubmitException, match="403"):
        executor._call("GET", "/api/v1/workloads")
    assert len(tokens) == 3


def test_http_error_does_not_expose_response_secrets(monkeypatch):
    mod = module()
    error = HTTPError(
        "https://runai.example/api/v1/token",
        403,
        "Forbidden",
        {},
        io.BytesIO(b'{"clientSecret":"private"}'),
    )
    monkeypatch.setattr(
        mod._opener, "open", lambda *a, **k: (_ for _ in ()).throw(error)
    )
    with pytest.raises(psij.SubmitException, match="HTTP 403") as caught:
        mod._request_json("https://runai.example/api/v1/token")
    assert "private" not in str(caught.value)


def test_transport_error_is_not_retried_and_names_ambiguous_submission(
    executor, monkeypatch
):
    backend(executor, monkeypatch)
    executor._target_ids = "C1", "P1"
    calls = []

    def call(*args):
        calls.append(args)
        raise psij.SubmitException("request timed out")

    monkeypatch.setattr(executor, "_call", call)
    native = job()
    with pytest.raises(psij.SubmitException, match=f"hiraku-{native.id}"):
        executor.submit(native)
    assert len(calls) == 1


def test_http_request_has_user_agent_and_timeout(monkeypatch):
    mod = module()
    seen = []

    def request(req, **kwargs):
        seen.append((req, kwargs))
        raise URLError("offline")

    monkeypatch.setattr(mod._opener, "open", request)
    with pytest.raises(psij.SubmitException, match="Cannot reach"):
        mod._request_json("https://runai.example/api/v1/token", method="POST", body={})
    req, kwargs = seen[0]
    assert req.get_header("User-agent") == "hiraku-runai/0.1"
    assert kwargs["timeout"] == mod.HTTP_TIMEOUT
