"""JSON persistence, PSI/J local execution, and the job CLI."""

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import psij
import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.errors import ExecutionError, RecordNotFoundError, ValidationError
from hiraku.jobs import Job, update_jobs, validate_command
from hiraku.project import Project
from hiraku.tracking import Leaderboard


@pytest.fixture
def project(tmp_path):
    project = Project.create(tmp_path, install_skill=False, install_mcp=False)
    project.set_active_record(project.vault.create("test", title="Test", type="task"))
    return project


@pytest.fixture
def remote_job(project):
    job = Job(
        id="b" * 32,
        record_id="test",
        spec=psij.JobSpec(executable="echo"),
        executor="remote",
        native_id="submission-1",
        status=psij.JobStatus(psij.JobState.QUEUED),
    )
    job._path = project.root / ".hiraku/jobs" / job.id / "job.json"
    job._path.parent.mkdir(parents=True)
    job._save()
    return job


@pytest.fixture
def backend(remote_job, monkeypatch):
    response = [psij.JobState.QUEUED]
    calls = []
    observers = []

    def observe(native):
        if isinstance(response[0], str):
            raise psij.SubmitException(response[0])
        native.status = psij.JobStatus(response[0])

    executor = SimpleNamespace(
        _notify_callback=lambda *args: None,
        refresh=lambda *args: pytest.fail("Hiraku must not query the backend"),
    )

    def attach(native, native_id):
        calls.append(native_id)
        native.executor = executor
        native._native_id = native_id
        observe(native)
        observers.append(native)

    executor.attach = attach
    monkeypatch.setattr(Job, "_executor", lambda self: executor)

    def respond(status):
        response[0] = status
        if not isinstance(status, str):
            for native in observers:
                observe(native)

    respond.calls = calls
    return respond


def test_update_keeps_progress_in_memory_and_saves_final(remote_job, backend):
    before = remote_job._path.read_bytes()
    backend(psij.JobState.ACTIVE)
    remote_job.update()
    assert remote_job.status.state == psij.JobState.ACTIVE
    assert remote_job.started_at
    assert remote_job._path.read_bytes() == before

    # A failed background observation does not replace the last real observation.
    backend("offline")
    remote_job.update()
    assert remote_job.status.state == psij.JobState.ACTIVE
    assert remote_job._path.read_bytes() == before
    backend(psij.JobState.COMPLETED)
    remote_job.update()
    assert remote_job.status.state == psij.JobState.COMPLETED
    assert Job._read(remote_job._path).status.state == psij.JobState.COMPLETED
    saved = remote_job._path.read_bytes()
    remote_job.update()
    assert remote_job._path.read_bytes() == saved
    assert backend.calls == [remote_job.native_id]
    assert not hasattr(remote_job, "observation_error")
    assert not hasattr(remote_job, "state")


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("running", psij.JobState.ACTIVE),
        ("hibernated", psij.JobState.CANCELED),
        ("offline", None),
    ],
)
def test_renku_attachment_observes_before_starting_monitor(
    monkeypatch, state, expected
):
    executor = psij.JobExecutor.get_instance(
        "renku", url="https://renkulab.io/p/ns/demo"
    )
    monitors = []
    calls = []

    def rnk(*args, **kwargs):
        calls.append(args)
        if state == "offline":
            raise psij.SubmitException("offline")
        return [{"name": "remote-1", "status": {"state": state}}]

    monkeypatch.setattr(executor, "_rnk", rnk)
    monkeypatch.setattr(executor, "_monitor", monitors.append)
    native = psij.Job(psij.JobSpec(executable="echo"))
    if state == "offline":
        with pytest.raises(psij.SubmitException, match="offline"):
            executor.attach(native, "remote-1")
        assert monitors == []
    else:
        executor.attach(native, "remote-1")
        assert native.status.state == expected
        assert monitors == [native]
    assert calls == [("job", "list")]


def test_final_outcome_save_can_be_retried(remote_job, backend, monkeypatch):
    backend(psij.JobState.COMPLETED)
    save = Job._save

    def fail(job):
        raise OSError("disk unavailable")

    monkeypatch.setattr(Job, "_save", fail)
    with pytest.raises(ExecutionError, match="disk unavailable"):
        remote_job.update()
    assert Job._read(remote_job._path).status.state == psij.JobState.QUEUED
    monkeypatch.setattr(Job, "_save", save)
    remote_job.update()
    assert Job._read(remote_job._path).status.state == psij.JobState.COMPLETED
    assert backend.calls == [remote_job.native_id]


@pytest.mark.parametrize("command", ["show", "list"])
def test_cli_shows_executor_observation(
    project, remote_job, monkeypatch, backend, command
):
    monkeypatch.chdir(project.root)
    backend(psij.JobState.COMPLETED)
    args = ["job", command]
    if command != "list":
        args.append(remote_job.id)
    runner = CliRunner()
    assert runner.invoke(main, [*args, "--cached", "--json"]).exit_code == 2
    assert Job._read(remote_job._path).status.state == psij.JobState.QUEUED
    result = runner.invoke(main, args)
    assert result.exit_code == 0, result.output
    assert Job._read(remote_job._path).status.state == psij.JobState.COMPLETED


def test_remote_submission_returns_after_acceptance(project, monkeypatch):
    def submit(native):
        native._native_id = "submission-2"
        native.status = psij.JobStatus(psij.JobState.QUEUED)

    monkeypatch.setattr(
        psij.JobExecutor,
        "get_instance",
        lambda *a, **kw: SimpleNamespace(submit=submit),
    )
    job = Job.create(["echo"], project=project, executor="remote").submit()
    saved = Job.load(job.id, project=project)
    assert saved.native_id == "submission-2"
    assert saved.status.state == psij.JobState.QUEUED


def test_create_persists_without_submitting(project, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Creation must not contact an executor")

    monkeypatch.setattr(psij.JobExecutor, "get_instance", unexpected)
    job = Job.create(["echo", "hello"], project=project)
    saved = Job.load(job.id, project=project)
    assert saved.status.state == psij.JobState.NEW
    assert saved.native_id is None and saved.submitted_at is None
    assert saved.spec.arguments == ["hello"]


@pytest.mark.parametrize("method", ["submit", "run"])
def test_attempt_cannot_be_submitted_twice(project, monkeypatch, method):
    calls = []

    def submit(native):
        calls.append(native)
        native._native_id = "remote-1"
        native.status = psij.JobStatus(psij.JobState.COMPLETED, exit_code=7)

    def get_instance(name, **kwargs):
        assert name == "remote" and kwargs["url"] == "https://executor"
        return SimpleNamespace(submit=submit)

    monkeypatch.setattr(
        psij.JobExecutor,
        "get_instance",
        get_instance,
    )
    job = Job.create(
        ["echo"], project=project, executor="remote", executor_url="https://executor"
    )
    stale = Job.load(job.id, project=project)
    assert getattr(job, method)() is job
    assert job.status.exit_code == 7
    before = job._path.read_bytes()
    for attempt in (job, stale, Job.load(job.id, project=project)):
        with pytest.raises(ExecutionError, match="already been submitted"):
            getattr(attempt, method)()
    assert len(calls) == 1
    assert job._path.read_bytes() == before


def test_run_waits_using_detached_executor(project):
    job = Job.create(
        [
            sys.executable,
            "-c",
            "import sys; print('out'); print('err',file=sys.stderr)",
        ],
        project=project,
    ).run()
    assert job.executor == "detached"
    assert job.status.exit_code == 0
    assert dict(job.logs()) == {"stdout": "out\n", "stderr": "err\n"}
    assert not (job._path.parent / "worker.log").exists()


def test_no_follow_saves_output_without_streaming(project, monkeypatch):
    monkeypatch.chdir(project.root)
    result = CliRunner().invoke(
        main,
        ["job", "run", "--no-follow", "--", sys.executable, "-c", "print('saved')"],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    job = Job.load(result.stderr.strip(), project=project)
    assert dict(job.logs())["stdout"] == "saved\n"


def test_missing_record_is_rejected_before_starting_runner(project):
    with pytest.raises(RecordNotFoundError, match="missing"):
        Job.create(
            ["echo", "hello"], project=project, executor="detached", record_id="missing"
        ).submit()


@pytest.mark.parametrize("executor", ["detached", "remote"])
def test_submit_uses_psij_for_every_executor(project, monkeypatch, executor):
    names = []

    def submit(native):
        native._native_id = "accepted"
        native.status = psij.JobStatus(psij.JobState.QUEUED)

    def get_instance(name, **kwargs):
        names.append(name)
        return SimpleNamespace(submit=submit)

    monkeypatch.setattr(psij.JobExecutor, "get_instance", get_instance)
    job = Job.create(["echo", "hello"], project=project, executor=executor).submit()
    assert job.native_id == "accepted"
    assert names == [executor]


def test_backend_submission_failure_is_persisted(project, monkeypatch):
    def fail(native):
        raise OSError("cannot submit")

    monkeypatch.setattr(
        psij.JobExecutor,
        "get_instance",
        lambda *a, **kw: SimpleNamespace(submit=fail),
    )
    with pytest.raises(ExecutionError, match="cannot submit"):
        Job.create(["echo"], project=project).submit()
    job = Job.list(project=project)[0]
    assert job.status.state == psij.JobState.FAILED
    with pytest.raises(ExecutionError, match="already been submitted"):
        job.submit()


def test_detached_local_preserves_resources_and_private_environment(project):
    job = Job.create(
        [sys.executable, "-c", "import os; print(os.environ['CUSTOM_VALUE'])"],
        project=project,
        env={"CUSTOM_VALUE": "private-value"},
        resources=psij.ResourceSpecV1(process_count=1),
    ).submit()
    assert job.executor == "detached"
    assert job.wait().exit_code == 0
    assert dict(job.logs())["stdout"] == "private-value\n"
    assert job.spec.resources.computed_process_count == 1
    assert "private-value" not in job._path.read_text()


def test_backend_completion_does_not_write_hiraku_snapshot(project):
    gate = project.root / "release"
    job = Job.create(
        [
            sys.executable,
            "-c",
            "import time; from pathlib import Path\n"
            f"while not Path({str(gate)!r}).exists(): time.sleep(.05)",
        ],
        project=project,
    ).submit()
    before = job._path.read_bytes()
    native = psij.Job()
    job._executor().attach(native, job.native_id)
    try:
        gate.touch()
        status = native.wait(timeout=timedelta(seconds=10))
        assert status and status.state == psij.JobState.COMPLETED
        assert job._path.read_bytes() == before
        job.wait()
        assert job.status.exit_code == 0
        assert job._path.read_bytes() != before
    finally:
        gate.touch()


@pytest.mark.parametrize("command", ["show", "list"])
def test_cli_attachment_failure_preserves_submission(
    project, remote_job, monkeypatch, backend, command
):
    monkeypatch.chdir(project.root)

    remote_job.updated_at = datetime.now(UTC) - timedelta(minutes=1)
    remote_job._path.write_text(remote_job.model_dump_json())
    before = remote_job._path.read_bytes()
    backend("backend timed out")
    args = ["job", command, *([remote_job.id] if command == "show" else [])]
    result = CliRunner().invoke(main, [*args, "--json"])
    assert result.exit_code == 0, result.output
    value = json.loads(result.output)
    value = value[0] if command == "list" else value
    assert value["status"]["state"] == "QUEUED"
    assert "observation_error" not in value
    assert value["updated_at"] == remote_job.updated_at.isoformat().replace(
        "+00:00", "Z"
    )
    assert not value["status"]["metadata"]
    assert "stale" not in CliRunner().invoke(main, args).output
    assert remote_job._path.read_bytes() == before
    assert Job._read(remote_job._path).status.state == psij.JobState.QUEUED


def test_final_job_update_skips_backend(remote_job, monkeypatch):
    earlier = Job._read(remote_job._path)
    remote_job.status = psij.JobStatus(psij.JobState.COMPLETED)
    remote_job._save()
    monkeypatch.setattr(
        Job,
        "_executor",
        lambda *a, **kw: pytest.fail("Final job must stay cached"),
    )
    remote_job.update()
    assert remote_job.status.state == psij.JobState.COMPLETED
    earlier.update()
    assert earlier.status.state == psij.JobState.COMPLETED


def test_update_jobs_continues_after_failure(remote_job, monkeypatch, caplog):
    other = remote_job.model_copy(update={"id": "c" * 32})
    calls = []

    def update(self):
        calls.append(self.id)
        if self.id == remote_job.id:
            raise ExecutionError("offline")

    monkeypatch.setattr(Job, "update", update)
    update_jobs([remote_job, other])
    assert calls == [remote_job.id, other.id]
    assert "offline" in caplog.text


def test_local_update_preserves_original_exit_code(project):
    gate = project.root / "finish"
    job = Job.create(
        [
            sys.executable,
            "-c",
            "import pathlib,time,sys; "
            f"p=pathlib.Path({str(gate)!r}); "
            "exec('while not p.exists(): time.sleep(.05)'); sys.exit(7)",
        ],
        project=project,
        executor="detached",
    ).submit()
    try:
        before = job.updated_at
        job.update()
        assert job.updated_at == before
        assert not job.status.final
    finally:
        gate.touch()
    assert job.wait().exit_code == 7


def test_missing_local_backend_state_preserves_job(remote_job, project):
    remote_job.executor = "detached"
    remote_job.executor_url = str(project.root / "backend")
    remote_job.native_id = "d" * 32
    remote_job._save()
    remote_job.updated_at = datetime.now(UTC) - timedelta(minutes=1)
    remote_job._path.write_text(remote_job.model_dump_json())
    with pytest.raises(ExecutionError, match="Cannot observe local job"):
        remote_job.update()
    assert Job._read(remote_job._path).status.state == psij.JobState.QUEUED


def test_snapshot_roundtrip_and_collision(project, monkeypatch):
    assert Job.list(project=project) == []
    assert not (project.root / ".hiraku/jobs").exists()
    job = Job.create([sys.executable, "-c", "pass"], project=project).run()
    assert job.wait().state == psij.JobState.COMPLETED
    loaded = Job.load(job.id, project=project)
    assert loaded.model_dump(mode="json") == job.model_dump(mode="json")
    assert loaded.spec.executable == sys.executable
    assert loaded.started_at and loaded.finished_at
    monkeypatch.setattr("hiraku.jobs.uuid4", lambda: SimpleNamespace(hex=job.id))
    with pytest.raises(ExecutionError, match="Cannot create"):
        Job.create(["echo", "collision"], project=project).run()
    assert Job.load(job.id, project=project).status.state == psij.JobState.COMPLETED
    with pytest.raises(ExecutionError):
        Job.load("../outside", project=project)
    path = project.root / ".hiraku/jobs" / job.id / "job.json"
    path.write_text("{")
    with pytest.raises(ExecutionError, match="Cannot read job"):
        Job.load(job.id, project=project)


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "../escape"},
        {"spec": {"executable": ""}},
        {"spec": {"executable": "echo", "arguments": ["bad\x00argument"]}},
        {"status": {"state": "unknown"}},
        {"created_at": "2026-01-01T00:00:00"},
    ],
)
def test_invalid_snapshots_are_rejected(project, changes):
    directory = project.root / ".hiraku/jobs" / ("a" * 32)
    directory.mkdir(parents=True)
    content = {
        "id": "a" * 32,
        "record_id": "test",
        "spec": {"executable": "echo"},
        **changes,
    }
    (directory / "job.json").write_text(json.dumps(content))
    with pytest.raises(ExecutionError, match="Cannot read job"):
        Job.load("a" * 32, project=project)


def test_background_survives_submitter_and_preserves_exit_code(project):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from hiraku import Job, Project; import sys; "
            "job = Job.create([sys.executable, '-c', "
            '"import time, os; time.sleep(1); '
            "print(os.environ['HIRAKU_JOB_ID']); raise SystemExit(7)\"], "
            f"project=Project.discover({str(project.root)!r})).submit(); "
            "print(job.id)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    job = Job.load(result.stdout.strip(), project=project)
    assert job.wait().state == psij.JobState.FAILED
    assert job.status.exit_code == 7
    assert job.native_id and job.submitted_at and job.finished_at
    assert dict(job.logs())["stdout"].strip() == job.id


def test_cancel_stops_execution(project):
    marker = project.root / "unexpected"
    gate = project.root / "release"
    job = Job.create(
        [
            sys.executable,
            "-c",
            "import time; from pathlib import Path\n"
            f"while not Path({str(gate)!r}).exists(): time.sleep(.05)\n"
            f"Path({str(marker)!r}).touch()",
        ],
        project=project,
        executor="detached",
    ).submit()
    try:
        Job.load(job.id, project=project).cancel()
        assert job.wait().state == psij.JobState.CANCELED
    finally:
        gate.touch()
    assert not marker.exists()


def test_launch_failure_is_persisted(project):
    job = Job.create(["/nonexistent/hiraku-test-command"], project=project).run()
    assert job.wait().state == psij.JobState.FAILED
    assert job.status.exit_code != 0


def test_unsubmitted_job_cannot_infer_success(project):
    directory = project.root / ".hiraku/jobs" / ("b" * 32)
    directory.mkdir(parents=True)
    job = Job(
        id="b" * 32,
        record_id="test",
        spec=psij.JobSpec(executable="echo"),
        created_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    (directory / "job.json").write_text(job.model_dump_json())
    job = Job.load(job.id, project=project)
    with pytest.raises(ExecutionError, match="no native ID"):
        job.wait()
    assert Job.load(job.id, project=project).status.state == psij.JobState.NEW


def test_job_attribution_precedes_filter_and_limit(project):
    job = Job.create([sys.executable, "-c", "pass"], project=project).run()
    job.wait()
    (project.root / "runs.csv").write_text(
        "id,hiraku_job_id,metric.score\n"
        f"one,{job.id},2\ntwo,{job.id},1\nforeign,unknown,0\n"
    )
    board = Leaderboard(source={"type": "csv", "path": "runs.csv"})
    runs = board.list_runs(project, record_id="test", sort="score", limit=1)
    assert [run.id for run in runs] == ["two"]
    assert runs[0].job_id == job.id and runs[0].record_id == "test"
    assert len(board.list_runs(project)) == 2
    assert len(board.list_runs(project, include_unattributed=True)) == 3


@pytest.mark.parametrize("code", [0, 4])
def test_foreground_cli_output_and_exit_code(project, monkeypatch, code):
    monkeypatch.chdir(project.root)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "job",
            "run",
            "--",
            sys.executable,
            "-c",
            f"import os; print(os.getcwd()); raise SystemExit({code})",
        ],
    )
    assert result.exit_code == code, result.output
    assert result.stdout.strip() == str(project.root)
    job = Job.load(result.stderr.strip(), project=project)
    assert job.status.exit_code == code
    assert runner.invoke(main, ["job", "wait", job.id]).exit_code == code
    assert runner.invoke(main, ["job", "run"]).exit_code != 0
    assert runner.invoke(main, ["job", "submit"]).exit_code != 0
    assert runner.invoke(main, ["run", "--help"]).exit_code != 0


@pytest.mark.parametrize("options", [[], ["--executor", "local"]])
def test_background_cli_and_inspection(project, monkeypatch, options):
    monkeypatch.chdir(project.root)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "job",
            "submit",
            *options,
            "--",
            sys.executable,
            "-c",
            "from pathlib import Path; import time\n"
            "deadline = time.monotonic() + 10\n"
            "while not Path('release').exists() and time.monotonic() < deadline:\n"
            "    time.sleep(0.05)\n"
            "print('hello')",
        ],
    )
    assert result.exit_code == 0, result.output
    job = Job.load(result.output.strip(), project=project)
    assert job.executor == "detached"
    assert not job.status.final
    (project.root / "release").touch()
    job.wait()
    shown = runner.invoke(main, ["job", "show", job.id, "--json"])
    assert json.loads(shown.output)["status"]["state"] == "COMPLETED"
    assert "COMPLETED" in runner.invoke(main, ["job", "show", job.id]).output
    assert runner.invoke(main, ["job", "logs", job.id]).output == "hello\n"
    listed = runner.invoke(main, ["job", "list", "--json"])
    assert json.loads(listed.output)[0]["id"] == job.id
    table = runner.invoke(main, ["job", "list"], env={"COLUMNS": "140"}).output
    assert job.id in table
    assert "executor" in table
    assert job.executor in table


@pytest.mark.parametrize("command", ["run", "submit"])
def test_background_option_is_removed(project, monkeypatch, command):
    monkeypatch.chdir(project.root)
    result = CliRunner().invoke(main, ["job", command, "--background", "--", "echo"])
    assert result.exit_code == 2
    assert "No such option '--background'" in result.output


def test_interrupting_foreground_submission_cancels(project, monkeypatch):
    monkeypatch.chdir(project.root)

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(Job, "_logs", interrupt)
    result = CliRunner().invoke(
        main,
        [
            "job",
            "run",
            "--",
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
        ],
    )
    assert result.exit_code != 0
    assert Job.list(project=project)[0].status.state == psij.JobState.CANCELED
    # A canceled foreground run must not prevent subsequent execution.
    job = Job.create([sys.executable, "-c", "pass"], project=project).run()
    assert job.status.exit_code == 0


def test_environment_and_provenance_are_separate(project, monkeypatch):
    monkeypatch.setenv("HIRAKU_JOB_ID", "stale")
    for args in (
        ["init", "-q"],
        ["config", "user.email", "test@example.invalid"],
        ["config", "user.name", "Test"],
        ["add", "."],
        ["commit", "-qm", "initial"],
    ):
        subprocess.run(["git", *args], cwd=project.root, check=True)
    (project.root / "dirty").touch()
    job = Job.create(
        [
            sys.executable,
            "-c",
            "import os, json; print(json.dumps({k:v for k,v in os.environ.items() "
            "if k.startswith('HIRAKU_')})); print(os.environ['CUSTOM_VALUE'])",
        ],
        project=project,
        env={"CUSTOM_VALUE": "private-value"},
    ).run()
    job.wait()
    lines = dict(job.logs())["stdout"].splitlines()
    assert json.loads(lines[0]) == {"HIRAKU_JOB_ID": job.id}
    assert lines[1] == "private-value"
    assert "private-value" not in job.model_dump_json()
    assert job.commit and job.git_state == "dirty"
    assert job.record_id == "test"


@pytest.mark.parametrize(
    "env",
    [
        {"HIRAKU_JOB_ID": "other"},
        {"bad=key": "value"},
        {"valid": "bad\x00value"},
        {"valid": 1},
    ],
)
def test_invalid_environment(project, env):
    with pytest.raises(ValidationError):
        Job.create(["echo", "hello"], project=project, env=env).run()


def test_explicit_script_runs_in_working_tree(project):
    script = project.root / "launch.sh"
    script.write_text('#!/bin/sh\n[ "$1" = "--" ] && shift\nexec "$@"\n')
    script.chmod(0o755)
    (project.root / "marker").write_text("working tree")
    job = Job.create(
        [str(script), "--", "/bin/sh", "-c", "cat marker"], project=project
    ).run()
    assert job.wait().exit_code == 0
    assert dict(job.logs())["stdout"] == "working tree"


def test_validate_command_accepts_none_and_copies_argv():
    assert validate_command(None) is None
    argv = ("python", "train.py")
    assert validate_command(argv) == list(argv)


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
    ],
)
def test_invalid_commands(project, value):
    with pytest.raises(ValidationError):
        validate_command(value)
    with pytest.raises(ValidationError):
        Job.create(value, project=project).run()
