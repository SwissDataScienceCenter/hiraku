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
from hiraku.errors import ExecutionError, ValidationError
from hiraku.jobs import Job, validate_command
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


def test_update_persists_and_preserves_snapshot_on_failure(remote_job, monkeypatch):
    monkeypatch.setattr(
        Job, "_observation", lambda self: psij.JobStatus(psij.JobState.ACTIVE)
    )
    remote_job.update()
    assert Job._read(remote_job._path).status.state == psij.JobState.ACTIVE
    assert remote_job.started_at

    def fail(self):
        raise ExecutionError("offline")

    monkeypatch.setattr(Job, "_observation", fail)
    before = remote_job._path.read_bytes()
    with pytest.raises(ExecutionError, match="offline"):
        remote_job.update()
    assert remote_job._path.read_bytes() == before
    assert Job._read(remote_job._path).status.state == psij.JobState.ACTIVE
    monkeypatch.setattr(
        Job, "_observation", lambda self: psij.JobStatus(psij.JobState.COMPLETED)
    )
    remote_job.update()
    assert remote_job.status.state == psij.JobState.COMPLETED
    assert not hasattr(remote_job, "observation_error")
    assert not hasattr(remote_job, "state")


@pytest.mark.parametrize(
    "args", [["submit", "--poll"], ["show", "id", "--refresh"], ["refresh", "id"]]
)
def test_monitoring_cli_removed(project, monkeypatch, args):
    monkeypatch.chdir(project.root)
    assert CliRunner().invoke(main, ["job", *args]).exit_code == 2


@pytest.mark.parametrize("command", ["show", "list"])
def test_cli_updates_unless_cached(project, remote_job, monkeypatch, command):
    monkeypatch.chdir(project.root)
    monkeypatch.setattr(
        Job, "_observation", lambda self: psij.JobStatus(psij.JobState.COMPLETED)
    )
    args = ["job", command]
    if command != "list":
        args.append(remote_job.id)
    runner = CliRunner()
    assert runner.invoke(main, [*args, "--cached", "--json"]).exit_code == 0
    assert Job._read(remote_job._path).status.state == psij.JobState.QUEUED
    result = runner.invoke(main, args)
    assert result.exit_code == 0, result.output
    assert Job._read(remote_job._path).status.state == psij.JobState.COMPLETED


def test_unmonitored_remote_owner_exits_after_submission(remote_job, monkeypatch):
    def submit(native):
        native._native_id = "submission-2"
        native.status = psij.JobStatus(psij.JobState.QUEUED)

    monkeypatch.setattr(
        psij.JobExecutor,
        "get_instance",
        lambda *a, **kw: SimpleNamespace(submit=submit),
    )
    Job._work(str(remote_job._path))
    saved = Job._read(remote_job._path)
    assert saved.native_id == "submission-2"
    assert saved.status.state == psij.JobState.QUEUED


@pytest.mark.parametrize("command", ["show", "list"])
def test_cli_unknown_preserves_last_status(project, remote_job, monkeypatch, command):
    monkeypatch.chdir(project.root)

    def fail(self):
        raise ExecutionError("backend timed out")

    monkeypatch.setattr(Job, "_observation", fail)
    args = ["job", command, *([remote_job.id] if command == "show" else [])]
    result = CliRunner().invoke(main, [*args, "--json"])
    assert result.exit_code == 0, result.output
    value = json.loads(result.output)
    value = value[0] if command == "list" else value
    assert value["state"] == "UNKNOWN"
    assert value["status"]["state"] == "QUEUED"
    assert value["observation_error"] == "backend timed out"
    assert not value["status"]["metadata"]
    assert "UNKNOWN" in CliRunner().invoke(main, args).output
    assert Job._read(remote_job._path).status.state == psij.JobState.QUEUED


def test_final_job_update_skips_backend(remote_job, monkeypatch):
    remote_job.status = psij.JobStatus(psij.JobState.COMPLETED)
    remote_job._save()
    monkeypatch.setattr(
        Job, "_observation", lambda self: pytest.fail("Final job must stay cached")
    )
    remote_job.update()
    assert remote_job.status.state == psij.JobState.COMPLETED


def test_local_update_preserves_original_exit_code(project):
    gate = project.root / "finish"
    job = Job.submit(
        [
            sys.executable,
            "-c",
            "import pathlib,time,sys; "
            f"p=pathlib.Path({str(gate)!r}); "
            "exec('while not p.exists(): time.sleep(.05)'); sys.exit(7)",
        ],
        project=project,
    )
    try:
        job.update()
        assert not job.status.final
    finally:
        gate.touch()
    assert job.wait().exit_code == 7


def test_local_update_without_owner_does_not_guess_exit_code(remote_job):
    remote_job.executor = "local"
    remote_job._save()
    remote_job.heartbeat_at = datetime.now(UTC) - timedelta(minutes=1)
    remote_job._save()
    with pytest.raises(ExecutionError, match="outcome unknown"):
        remote_job.update()
    assert Job._read(remote_job._path).status.state == psij.JobState.QUEUED


def test_snapshot_roundtrip_and_collision(project, monkeypatch):
    assert Job.list(project=project) == []
    assert not (project.root / ".hiraku/jobs").exists()
    job = Job.submit([sys.executable, "-c", "pass"], project=project)
    assert job.wait().state == psij.JobState.COMPLETED
    loaded = Job.load(job.id, project=project)
    assert loaded.model_dump(mode="json") == job.model_dump(mode="json")
    assert loaded.spec.executable == sys.executable
    assert loaded.started_at and loaded.finished_at
    monkeypatch.setattr("hiraku.jobs.uuid4", lambda: SimpleNamespace(hex=job.id))
    with pytest.raises(ExecutionError, match="Cannot create"):
        Job.submit(["echo", "collision"], project=project)
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
            "job = Job.submit([sys.executable, '-c', "
            '"import time, os; time.sleep(1); '
            "print(os.environ['HIRAKU_JOB_ID']); raise SystemExit(7)\"], "
            f"project=Project.discover({str(project.root)!r})); print(job.id)",
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
    job = Job.submit(
        [
            sys.executable,
            "-c",
            "import time; from pathlib import Path\n"
            f"while not Path({str(gate)!r}).exists(): time.sleep(.05)\n"
            f"Path({str(marker)!r}).touch()",
        ],
        project=project,
    )
    try:
        job.cancel()
        assert job.wait().state == psij.JobState.CANCELED
    finally:
        gate.touch()
    assert not marker.exists()


def test_launch_failure_is_persisted(project):
    job = Job.submit(["/nonexistent/hiraku-test-command"], project=project)
    assert job.wait().state == psij.JobState.FAILED
    assert job.status.exit_code != 0


def test_stale_monitor_is_unknown_not_success(project):
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
    with pytest.raises(ExecutionError, match="outcome unknown"):
        job.wait()
    assert Job.load(job.id, project=project).status.state == psij.JobState.NEW


def test_job_attribution_precedes_filter_and_limit(project):
    job = Job.submit([sys.executable, "-c", "pass"], project=project)
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


def test_background_cli_and_inspection(project, monkeypatch):
    monkeypatch.chdir(project.root)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "job",
            "submit",
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
    assert not job.status.final
    (project.root / "release").touch()
    job.wait()
    shown = runner.invoke(main, ["job", "show", job.id, "--json"])
    assert json.loads(shown.output)["status"]["state"] == "COMPLETED"
    assert "COMPLETED" in runner.invoke(main, ["job", "show", job.id]).output
    assert runner.invoke(main, ["job", "logs", job.id]).output == "hello\n"
    listed = runner.invoke(main, ["job", "list", "--json"])
    assert json.loads(listed.output)[0]["id"] == job.id
    table = runner.invoke(main, ["job", "list"]).output
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

    monkeypatch.setattr(Job, "logs", interrupt)
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
    job = Job.submit(
        [
            sys.executable,
            "-c",
            "import os, json; print(json.dumps({k:v for k,v in os.environ.items() "
            "if k.startswith('HIRAKU_')})); print(os.environ['CUSTOM_VALUE'])",
        ],
        project=project,
        env={"CUSTOM_VALUE": "private-value"},
    )
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
        Job.submit(["echo", "hello"], project=project, env=env)


def test_explicit_script_runs_in_working_tree(project):
    script = project.root / "launch.sh"
    script.write_text('#!/bin/sh\n[ "$1" = "--" ] && shift\nexec "$@"\n')
    script.chmod(0o755)
    (project.root / "marker").write_text("working tree")
    job = Job.submit(
        [str(script), "--", "/bin/sh", "-c", "cat marker"], project=project
    )
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
        Job.submit(value, project=project)
