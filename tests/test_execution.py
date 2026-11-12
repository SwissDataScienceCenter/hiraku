"""Native PSI/J execution, attribution, and CLI behavior."""

import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from threading import Event

import psij
import psutil
import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.errors import RecordNotFoundError, ValidationError
from hiraku.execution import describe_job, prepare_job, run_local, validate_command
from hiraku.project import Project


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(
        psij.JobExecutorConfig, "DEFAULT_WORK_DIRECTORY", tmp_path / "psij"
    )
    project = Project.create(
        tmp_path / "project with spaces", install_skill=False, install_mcp=False
    )
    project.set_active_record(project.vault.create("test", title="Test", type="task"))
    return project


@pytest.fixture
def detached(project):
    return psij.JobExecutor.get_instance("local-detached")


def wait(job):
    status = job.wait(timeout=timedelta(seconds=15))
    assert status is not None, "Job did not finish"
    return status


def test_preparation_returns_native_job_without_io(project):
    job = prepare_job(["echo", "hello"], project=project, record_id="test")
    assert type(job) is psij.Job
    assert job.status.state == psij.JobState.NEW
    assert job.executor is None and job.native_id is None
    assert job.spec.arguments == ["hello"]
    assert job.spec.environment == {
        "HIRAKU_RECORD_ID": "test",
        "HIRAKU_JOB_ID": job.id,
    }
    assert job.spec.stdout_path is None and job.spec.directory is None
    assert not (project.root / ".hiraku/jobs").exists()
    with pytest.raises(RecordNotFoundError):
        prepare_job(["echo"], project=project, record_id="missing")


def test_python_attribution_is_explicit(project, monkeypatch, capsys):
    monkeypatch.setenv("HIRAKU_RECORD_ID", "stale")
    monkeypatch.setenv("HIRAKU_JOB_ID", "stale")
    job = prepare_job(
        [
            sys.executable,
            "-c",
            "import os; print(repr(os.environ['HIRAKU_RECORD_ID']))",
        ],
        project=project,
        env={"CUSTOM": "value"},
    )
    assert job.spec.environment["HIRAKU_RECORD_ID"] == ""
    assert job.spec.environment["CUSTOM"] == "value"
    assert run_local(job).exit_code == 0
    assert capsys.readouterr().out == "''\n"


@pytest.mark.parametrize("explicit", [False, True])
def test_execution_directory(project, monkeypatch, capsys, tmp_path, explicit):
    monkeypatch.chdir(project.root)
    job = prepare_job(
        [sys.executable, "-c", "import os; print(os.getcwd())"],
        project=project,
        directory=project.root if explicit else None,
    )
    monkeypatch.chdir(tmp_path)
    assert run_local(job).exit_code == 0
    assert capsys.readouterr().out.strip() == str(
        project.root if explicit else tmp_path
    )


def test_foreground_retains_explicit_output(project, capsys):
    job = prepare_job([sys.executable, "-c", "print('saved')"], project=project)
    output = project.root / "saved output.log"
    job.spec.stdout_path = output
    assert run_local(job).exit_code == 0
    assert capsys.readouterr().out == "saved\n"
    assert job.spec.stdout_path == output
    assert output.read_text() == "saved\n"


@pytest.mark.parametrize("code", [0, 7])
def test_foreground_output_and_status(project, capsys, code):
    job = prepare_job(
        [
            sys.executable,
            "-c",
            "import sys; print('héllo'); print('error', file=sys.stderr); "
            f"raise SystemExit({code})",
        ],
        project=project,
    )
    assert run_local(job).exit_code == code
    output = capsys.readouterr()
    assert output.out == "héllo\n" and output.err == "error\n"
    assert job.spec.stdout_path is None and job.spec.stderr_path is None
    assert not (project.root / ".hiraku/jobs").exists()
    with pytest.raises(psij.InvalidJobException):
        run_local(job)


def test_foreground_streams_before_completion(project):
    command = (
        "import sys; from hiraku.project import Project; "
        "from hiraku.execution import prepare_job,run_local; "
        f"project=Project.discover({str(project.root)!r}); "
        "run_local(prepare_job([sys.executable,'-c',"
        "'import time; print(\"ready\",flush=True); time.sleep(1)'],project=project))"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", command], stdout=subprocess.PIPE, text=True
    )
    try:
        assert process.stdout.readline() == "ready\n"
        assert process.poll() is None
        assert process.wait(timeout=10) == 0
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()


@pytest.mark.parametrize("hook", ["pre_launch", "post_launch"])
@pytest.mark.parametrize("background", [False, True])
def test_hook_failure(project, detached, capsys, hook, background):
    path = project.root / "failing hook.sh"
    path.write_text("exit 9\n")
    job = prepare_job([sys.executable, "-c", "pass"], project=project)
    setattr(job.spec, hook, path)
    if background:
        detached.submit(job)
        status = wait(job)
        attached = psij.Job()
        detached.attach(attached, job.native_id)
        assert wait(attached).exit_code == 9
    else:
        status = run_local(job)
    assert status.state == psij.JobState.FAILED
    assert status.exit_code == 9


def test_detached_is_independent_of_hiraku(project, detached):
    # No preparation helper, attribution variables, or project metadata required.
    job = psij.Job(
        psij.JobSpec(sys.executable, ["-c", "print('hello'); raise SystemExit(7)"])
    )
    notifications = []
    job.set_job_status_callback(
        lambda observed, status: notifications.append(observed.native_id)
    )
    detached.submit(job)
    assert wait(job).exit_code == 7
    assert job.native_id in detached.list()
    assert notifications and set(notifications) == {job.native_id}
    assert job.spec.stdout_path.read_text() == "hello\n"
    assert not list(project.root.rglob("job.json"))
    attached = psij.Job()
    detached.attach(attached, job.native_id)
    assert wait(attached).state == psij.JobState.FAILED
    assert attached.status.exit_code == 7
    with pytest.raises(psij.InvalidJobException):
        detached.submit(job)


def test_attach_pending_job_and_callback(project, detached):
    gate = project.root / "release"
    command = (
        "from pathlib import Path; import time; "
        f"p=Path({str(gate)!r}); "
        "exec('while not p.exists(): time.sleep(.01)'); raise SystemExit(3)"
    )
    job = psij.Job(psij.JobSpec(sys.executable, ["-c", command]))
    detached.submit(job)
    attached = psij.Job()
    done = Event()
    attached.set_job_status_callback(
        lambda _, status: done.set() if status.final else None
    )
    try:
        detached.attach(attached, job.native_id)
        assert not attached.status.final
        gate.touch()
        assert wait(attached).exit_code == 3
        assert done.wait(timeout=2)
    finally:
        gate.touch()
        wait(job)


def test_detached_preserves_output_environment_and_resources(project, detached):
    output = project.root / "custom output.log"
    job = prepare_job(
        [sys.executable, "-c", "import os; print(os.environ['CUSTOM'])"],
        project=project,
        env={"CUSTOM": "private value"},
        resources=psij.ResourceSpecV1(process_count=1),
    )
    job.spec.stdout_path = output
    detached.submit(job)
    assert wait(job).exit_code == 0
    assert output.read_text() == "private value\n"
    assert job.spec.resources.computed_process_count == 1


def test_detached_rejects_unsupported_launcher_and_resources(project, detached):
    for field, value in [
        ("launcher", "quoted-single"),
        ("resources", psij.ResourceSpecV1(process_count=2)),
    ]:
        job = prepare_job(["echo"], project=project)
        setattr(job.spec, field, value)
        with pytest.raises(psij.InvalidJobException):
            detached.submit(job)


@pytest.mark.parametrize("native_id", ["../escape", "unknown", "a" * 36])
def test_detached_rejects_unknown_ids(detached, native_id):
    with pytest.raises(psij.InvalidJobException):
        detached.attach(psij.Job(), native_id)


def test_malformed_result_is_not_success(project, detached):
    job = psij.Job(psij.JobSpec(sys.executable, ["-c", "pass"]))
    detached.submit(job)
    wait(job)
    (detached.jobs_dir / job.native_id / "result.json").write_text("{")
    with pytest.raises(ValueError):
        detached.attach(psij.Job(), job.native_id)


def test_background_survives_submitter(project, detached):
    code = (
        "import psij,sys; "
        f"e=psij.JobExecutor.get_instance('local-detached',url={detached.url!r}); "
        "j=psij.Job(psij.JobSpec(sys.executable,['-c',"
        "'import time; time.sleep(.5); raise SystemExit(7)'])); "
        "e.submit(j); print(j.native_id)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    attached = psij.Job()
    detached.attach(attached, result.stdout.strip())
    assert wait(attached).exit_code == 7


def test_missing_result_does_not_infer_success(project, detached):
    # An interrupted launcher may leave only its backend directory.
    job = psij.Job()
    (detached.jobs_dir / job.id).mkdir(parents=True)
    detached.attach(job, job.id)
    assert job.wait(timeout=timedelta(seconds=0.2)) is None
    assert not job.status.final


@pytest.mark.parametrize("code", [0, 4])
def test_cli_foreground(project, monkeypatch, code):
    monkeypatch.chdir(project.root)
    result = CliRunner().invoke(
        main,
        [
            "run",
            "--",
            sys.executable,
            "-c",
            f"print('hello'); raise SystemExit({code})",
        ],
    )
    assert result.exit_code == code, result.output
    assert result.stdout == "hello\n"
    assert not (project.root / ".hiraku/jobs").exists()


def test_cli_submit_and_status_without_project(
    project, detached, monkeypatch, tmp_path
):
    monkeypatch.chdir(project.root)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "job",
            "submit",
            "--executor-url",
            detached.url,
            "--",
            sys.executable,
            "-c",
            "raise SystemExit(7)",
        ],
    )
    assert result.exit_code == 0, result.output
    receipt = json.loads(result.output)
    monkeypatch.chdir(tmp_path)
    args = [
        "job",
        "status",
        receipt["native_id"],
        "--executor-url",
        receipt["executor_url"],
        "--wait",
        "--json",
    ]
    status = runner.invoke(main, args)
    assert status.exit_code == 0, status.output
    assert json.loads(status.output)["status"]["exit_code"] == 7
    assert "FAILED" in runner.invoke(main, args[:-1]).output
    assert not (project.root / ".hiraku/jobs").exists()
    assert runner.invoke(main, ["job", "list"]).exit_code != 0
    assert runner.invoke(main, ["job", "show"]).exit_code != 0


def test_interrupting_foreground_cleans_up(project, monkeypatch):
    job = prepare_job(
        [sys.executable, "-c", "import time; time.sleep(30)"], project=project
    )
    original = psij.Job.wait
    interrupted = False

    def interrupt(self, *args, **kwargs):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        return original(self, *args, **kwargs)

    monkeypatch.setattr(psij.Job, "wait", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_local(job)
    assert job.status.state == psij.JobState.CANCELED
    assert not psutil.pid_exists(int(job.native_id))


@pytest.mark.parametrize(
    "env",
    [
        {"HIRAKU_JOB_ID": "other"},
        {"HIRAKU_RECORD_ID": "other"},
        {"bad=key": "value"},
        {"valid": "bad\x00value"},
        {"valid": 1},
    ],
)
def test_invalid_environment(project, env):
    with pytest.raises(ValidationError):
        prepare_job(["echo"], project=project, env=env)


def test_validate_command_copies_argv():
    assert validate_command(None) is None
    assert validate_command(("python", "train.py")) == ["python", "train.py"]


@pytest.mark.parametrize(
    "value",
    [
        "python train.py",
        b"python",
        {},
        [],
        [""],
        [1],
        ["python", None],
        ["python", "x\x00y"],
        None,
    ],
)
def test_invalid_commands(project, value):
    with pytest.raises(ValidationError):
        prepare_job(value, project=project)


def test_describe_job_uses_native_final_status():
    job = psij.Job()
    job.executor = psij.JobExecutor.get_instance("local")
    job.status = psij.JobStatus(psij.JobState.FAILED, exit_code=7)
    assert describe_job(job)["status"] == {
        "state": "FAILED",
        "exit_code": 7,
        "message": None,
    }


@pytest.fixture
def renku(monkeypatch):
    executor = psij.JobExecutor.get_instance(
        "renku", url="https://renkulab.io/p/namespace/project"
    )
    monkeypatch.setattr(executor, "poll_interval", 0.01)
    return executor


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("succeeded", psij.JobState.COMPLETED),
        ("failed", psij.JobState.FAILED),
        ("hibernated", psij.JobState.CANCELED),
    ],
)
def test_renku_submission_and_final_status(renku, monkeypatch, state, expected):
    calls = []

    def rnk(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("job", "start"):
            return {"name": "remote-1", "status": {"state": "starting"}}
        return [{"name": "remote-1", "status": {"state": state, "message": "remote"}}]

    monkeypatch.setattr(renku, "_rnk", rnk)
    job = psij.Job(
        psij.JobSpec(
            "python",
            ["train.py", "argument with spaces"],
            directory=Path("remote work"),
            environment={"CUSTOM": "value with spaces"},
            attributes=psij.JobAttributes(
                custom_attributes={"renku.launcher": "launcher"}
            ),
        )
    )
    renku.submit(job)
    assert job.native_id == "remote-1"
    status = wait(job)
    assert status.state == expected
    assert status.exit_code is None
    assert status.message == "remote"
    assert "cd 'remote work' && exec env 'CUSTOM=value with spaces'" in calls[0][-1]
    assert "renku.launcher" not in calls[0]
    attached = psij.Job()
    renku.attach(attached, "remote-1")
    assert wait(attached).state == expected
    assert renku.list() == ["remote-1"]


def test_renku_observation_error_does_not_invent_failure(renku, monkeypatch):
    attempts = 0

    def rnk(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return [{"name": "remote", "status": {"state": "running"}}]
        if attempts == 2:
            raise psij.SubmitException("temporary outage")
        return [{"name": "remote", "status": {"state": "succeeded"}}]

    monkeypatch.setattr(renku, "_rnk", rnk)
    job = psij.Job()
    renku.attach(job, "remote")
    assert wait(job).state == psij.JobState.COMPLETED
    assert attempts >= 3


def test_renku_missing_or_unknown_status_is_not_success(renku, monkeypatch):
    monkeypatch.setattr(renku, "_rnk", lambda *a, **k: [])
    with pytest.raises(psij.SubmitException, match="unavailable"):
        renku.attach(psij.Job(), "missing")
    monkeypatch.setattr(
        renku,
        "_rnk",
        lambda *a, **k: [
            {"name": "unknown", "status": {"state": "unexpected"}},
        ],
    )
    with pytest.raises(psij.SubmitException, match="Unknown Renku state"):
        renku.attach(psij.Job(), "unknown")


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


def test_renku_cli_invocation(renku, monkeypatch):
    def run(command, **kwargs):
        assert command == [
            "rnk",
            "--renku-url",
            "https://renkulab.io",
            "--format",
            "json",
            "--project-context",
            "namespace/project",
            "job",
            "list",
        ]
        assert kwargs["timeout"] == 20 and kwargs["check"]
        return subprocess.CompletedProcess(command, 0, stdout="[]")

    monkeypatch.setattr(subprocess, "run", run)
    assert renku.list() == []


@pytest.mark.parametrize("url", [None, "https://renkulab.io", "file:///p/ns/project"])
def test_renku_requires_project_target(url):
    with pytest.raises(ValueError, match="namespace/project"):
        psij.JobExecutor.get_instance("renku", url=url)
