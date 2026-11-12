"""JSON persistence, PSI/J local execution, and the job CLI."""

import json
import os
import select
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace

import psij
import psutil
import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.errors import (
    ExecutionError,
    JobNotFoundError,
    RecordNotFoundError,
    ValidationError,
)
from hiraku.jobs import Job, validate_command
from hiraku.project import Project


def finish(job):
    deadline = time.monotonic() + 15
    project = Project.discover(job.path.parent)
    while not job.status.final:
        assert time.monotonic() < deadline, "Job did not finish"
        job = Job.load(job.id, project=project)
        time.sleep(0.05)
    return job.status


def execute(job, method):
    return job.run() if method == "run" else job.submit(executor="hiraku-local")


@pytest.fixture
def project(tmp_path):
    project = Project.create(
        tmp_path / "project with spaces", install_skill=False, install_mcp=False
    )
    project.set_active_record(project.vault.create("test", title="Test", type="task"))
    return project


@pytest.mark.parametrize("method", ["run", "submit"])
def test_jobs_without_record_do_not_select_active_record(project, monkeypatch, method):
    monkeypatch.setenv("HIRAKU_RECORD_ID", "stale")
    job = Job.create(
        [
            sys.executable,
            "-c",
            "import os; print(repr(os.environ['HIRAKU_RECORD_ID']))",
        ],
        project=project,
    )
    assert job.record_id is None
    assert not job.path.parent.exists()
    execute(job, method)
    finish(job)
    assert job.spec.stdout_path.read_text() == "''\n"
    assert Job.load(job.id, project=project).record_id is None
    project.clear_active_record()
    job = Job.create(["echo"], record_id="test", project=project)
    assert job.record_id == "test"
    assert not job.path.parent.exists()


@pytest.mark.parametrize("method", ["run", "submit"])
@pytest.mark.parametrize("explicit", [False, True])
def test_create_fixes_execution_directory(
    project, monkeypatch, tmp_path, method, explicit
):
    working = tmp_path / "execution directory"
    working.mkdir()
    monkeypatch.chdir(working)
    job = Job.create(
        [sys.executable, "-c", "import os; print(os.getcwd())"],
        project=project,
        directory=project.root if explicit else None,
    )
    expected = project.root if explicit else working
    assert job.spec.directory == expected
    monkeypatch.chdir(tmp_path)
    execute(job, method)
    assert finish(job).exit_code == 0
    assert job.spec.stdout_path.read_text().strip() == str(expected)
    assert Job.load(job.id, project=project).spec.directory == expected


def test_create_does_not_save_or_submit(project, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Creation must not contact an executor")

    monkeypatch.setattr(
        type(psij.JobExecutor.get_instance("local")), "submit", unexpected
    )
    job = Job.create(["echo", "hello"], record_id="test", project=project)
    assert job.status.state == psij.JobState.NEW
    assert job.native_id is None and job.submitted_at is None
    assert job.executor is None
    assert job.model_dump(mode="json")["executor"] is None
    assert "path" not in job.model_dump(mode="json")
    assert job.path == project.root / ".hiraku" / "jobs" / job.id / "job.json"
    assert job.spec.arguments == ["hello"]
    assert str(job.spec.stdin_path) == os.devnull
    assert not job.path.parent.parent.exists()
    assert Job.list(project=project) == []
    with pytest.raises(JobNotFoundError):
        Job.load(job.id, project=project)


@pytest.mark.parametrize("method", ["run", "submit"])
def test_attempt_cannot_be_submitted_twice(project, method):
    job = Job.create(
        [sys.executable, "-c", "raise SystemExit(7)"], record_id="test", project=project
    )
    stale = job.model_copy()
    result = execute(job, method)
    assert result is job if method == "run" else isinstance(result, psij.Job)
    assert finish(job).exit_code == 7
    before = job.path.read_bytes()
    for attempt in (job, stale, Job.load(job.id, project=project)):
        with pytest.raises(ExecutionError, match="already been submitted"):
            execute(attempt, method)
    assert job.path.read_bytes() == before


def test_concurrent_submission_launches_once(project, monkeypatch):
    calls = []
    monkeypatch.setattr(
        type(psij.JobExecutor.get_instance("hiraku-local")),
        "submit",
        lambda self, native: calls.append(native),
    )
    job = Job.create(["echo"], record_id="test", project=project)
    barrier = Barrier(2)

    def submit(attempt):
        barrier.wait(timeout=5)
        try:
            attempt.submit(executor="hiraku-local")
        except ExecutionError as exc:
            return str(exc)
        return "submitted"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, [job, job.model_copy()]))
    assert outcomes.count("submitted") == 1
    assert any("already been submitted" in outcome for outcome in outcomes)
    assert len(calls) == 1
    assert not job.path.with_suffix(".lock").exists()


def test_run_records_local_result(project):
    job = Job.create(
        [
            sys.executable,
            "-c",
            "import sys; print('out'); print('err',file=sys.stderr)",
        ],
        record_id="test",
        project=project,
    ).run()
    assert job.executor.name == "local"
    assert job.status.exit_code == 0
    assert job.spec.stdout_path.read_text() == "out\n"
    assert job.spec.stderr_path.read_text() == "err\n"


def test_submit_returns_live_psij_job(project):
    job = Job.create([sys.executable, "-c", "pass"], project=project)
    native = job.submit(executor="hiraku-local")
    assert isinstance(native, psij.Job)
    assert native.native_id == job.native_id
    assert native.wait(timeout=timedelta(seconds=10)).state == psij.JobState.COMPLETED


def test_native_run_records_foreground_outcome_with_spaces(project, monkeypatch):
    native_executor_type = type(psij.JobExecutor.get_instance("local"))
    submitted = []
    original_submit = native_executor_type.submit

    def submit(executor, native):
        assert native.spec is job.spec
        submitted.append(type(executor))
        original_submit(executor, native)

    monkeypatch.setattr(native_executor_type, "submit", submit)
    job = Job.create(
        [sys.executable, "-c", "print('native'); raise SystemExit(7)"],
        record_id="test",
        project=project,
        directory=project.root,
    )
    assert job.spec.launcher == "hiraku-single"
    job.run()
    assert submitted == [native_executor_type]
    assert job.executor.name == "local"
    assert job.status.exit_code == 7
    assert Job.load(job.id, project=project).status.exit_code == 7
    assert job.spec.stdout_path.read_text() == "native\n"
    assert not job.path.with_name("result.json").exists()


def test_python_run_streams_and_saves_output(project, capsys):
    job = Job.create(
        [
            sys.executable,
            "-c",
            "import sys; print('out'); print('err', file=sys.stderr)",
        ],
        record_id="test",
        project=project,
    ).run()
    captured = capsys.readouterr()
    assert captured.out == "out\n"
    assert captured.err == "err\n"
    assert job.spec.stdout_path.read_text() == "out\n"
    assert job.spec.stderr_path.read_text() == "err\n"


def test_run_streams_before_command_finishes(project):
    gate = project.root / "continue"
    command = (
        "from pathlib import Path; import time; "
        "print('ready', flush=True); "
        f"p = Path({str(gate)!r}); "
        "exec('while not p.exists(): time.sleep(0.05)')"
    )
    script = (
        "from hiraku import Job, Project; import sys; "
        f"project = Project.discover({str(project.root)!r}); "
        f"Job.create([sys.executable, '-c', {command!r}], project=project).run()"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        assert select.select([process.stdout], [], [], 10)[0]
        assert process.stdout.readline() == b"ready\n"
        assert process.poll() is None
        gate.touch()
        assert process.wait(timeout=10) == 0
    finally:
        gate.touch()
        if process.poll() is None:
            process.kill()
            process.wait()
        process.communicate()


@pytest.mark.parametrize("method", ["run", "submit"])
@pytest.mark.parametrize(("hook", "code"), [("pre_launch", 13), ("post_launch", 17)])
def test_hook_failure_is_recorded(project, hook, code, method):
    script = project.root / "failing hook.sh"
    script.write_text(f"return {code}\n")
    job = Job.create(
        [sys.executable, "-c", "print('ran')"], record_id="test", project=project
    )
    setattr(job.spec, hook, script)
    execute(job, method)
    finish(job)
    saved = Job.load(job.id, project=project)
    assert saved.status.state == psij.JobState.FAILED
    assert saved.status.exit_code == code
    if hook == "pre_launch":
        assert not saved.spec.stdout_path.exists()
    else:
        assert saved.spec.stdout_path.read_text() == "ran\n"


def test_fast_result_does_not_race_with_submission_metadata(project):
    job = Job.create(["/bin/bash", "-c", "exit 7"], record_id="test", project=project)
    job.submit(executor="hiraku-local")
    assert finish(job).exit_code == 7
    result = job.path.with_name("result.json")
    before = result.read_bytes()
    job._save()  # Saving identity cannot overwrite the independent outcome.
    assert result.read_bytes() == before
    assert Job.load(job.id, project=project).status.exit_code == 7


@pytest.mark.parametrize(
    "result",
    [
        "{",
        '{"exit_code":-1,"finished_at":1}',
        '{"exit_code":"0","finished_at":1}',
        '{"exit_code":0,"finished_at":"2026-01-01T00:00:00"}',
    ],
)
def test_malformed_result_is_an_error(project, result):
    job = Job.create(["echo"], record_id="test", project=project)
    job.submit(executor="hiraku-local")
    finish(job)
    job.path.with_name("result.json").write_text(result)
    with pytest.raises(ExecutionError, match="Cannot read job"):
        Job.load(job.id, project=project)


def test_killed_detached_launcher_leaves_an_unknown_outcome(project):
    marker = project.root / "started"
    job = Job.create(
        [
            sys.executable,
            "-c",
            "import os,time; from pathlib import Path; "
            f"Path({str(marker)!r}).write_text(str(os.getsid(0))); time.sleep(30)",
        ],
        record_id="test",
        project=project,
    )
    job.submit(executor="hiraku-local")
    try:
        deadline = time.monotonic() + 10
        while not marker.exists():
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert marker.read_text() == job.native_id
        os.killpg(int(job.native_id), signal.SIGKILL)
        deadline = time.monotonic() + 5
        while True:
            try:
                os.kill(int(job.native_id), 0)
            except ProcessLookupError:
                break
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert not job.path.with_name("result.json").exists()
        assert not Job.load(job.id, project=project).status.final
    finally:
        with suppress(ProcessLookupError):
            os.killpg(int(job.native_id), signal.SIGKILL)


def test_renku_submission_only_saves_identity(project, monkeypatch):
    executor_type = type(psij.JobExecutor.get_instance("renku"))
    calls = []

    def rnk(self, *args, **kwargs):
        calls.append(args[:2])
        return {"name": "remote-1"}

    monkeypatch.setattr(executor_type, "_rnk", rnk)
    job = Job.create(
        ["python", "train.py"],
        record_id="test",
        project=project,
        attributes=psij.JobAttributes(custom_attributes={"renku.launcher": "launcher"}),
    )
    native = job.submit(
        executor=psij.JobExecutor.get_instance(
            "renku", url="https://renkulab.io/p/ns/demo"
        )
    )
    assert isinstance(native, psij.Job)
    assert native.native_id == "remote-1"
    assert calls == [("job", "start")]
    assert job.native_id == "remote-1"
    saved = Job.load(job.id, project=project)
    assert isinstance(saved.executor, psij.JobExecutor)
    assert saved.executor.name == "renku"
    assert saved.executor.url == "https://renkulab.io/p/ns/demo"
    assert saved.model_dump(mode="json")["executor"] == "renku"
    assert saved.executor_url == saved.executor.url
    assert saved.status.state == psij.JobState.QUEUED
    assert not job.path.with_name("result.json").exists()


def test_run_uses_default_native_executor(project):
    job = Job.create(["echo"], record_id="test", project=project)
    assert job.executor is None
    job.run()
    assert job.executor.name == "local"
    assert Job.load(job.id, project=project).status.exit_code == 0


def test_submit_rejects_invalid_executor_before_saving(project):
    job = Job.create(["echo"], record_id="test", project=project)
    with pytest.raises(ValidationError, match="executor name or instance"):
        job.submit(executor=123)
    with pytest.raises(TypeError):
        job.submit()
    assert not job.path.parent.exists()


@pytest.mark.parametrize("method", ["run", "submit"])
def test_cli_allows_jobs_without_an_active_record(project, monkeypatch, method):
    project.clear_active_record()
    monkeypatch.chdir(project.root)
    result = CliRunner().invoke(
        main, ["job", method, "--", sys.executable, "-c", "pass"]
    )
    assert result.exit_code == 0, result.output
    job = Job.list(project=project)[0]
    assert finish(job).exit_code == 0
    assert job.record_id is None


def test_cli_run_streams_and_saves_output(project, monkeypatch):
    monkeypatch.chdir(project.root)
    result = CliRunner().invoke(
        main,
        ["job", "run", "--", sys.executable, "-c", "print('saved')"],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout == "saved\n"
    job = Job.load(result.stderr.strip(), project=project)
    assert job.spec.stdout_path.read_text() == "saved\n"


def test_missing_record_is_rejected_before_starting_runner(project):
    with pytest.raises(RecordNotFoundError, match="missing"):
        Job.create(
            ["echo", "hello"],
            project=project,
            record_id="missing",
        ).submit(executor="hiraku-local")


def test_backend_submission_failure_is_persisted(project, monkeypatch):
    def fail(self, native):
        raise OSError("cannot submit")

    monkeypatch.setattr(
        type(psij.JobExecutor.get_instance("hiraku-local")), "submit", fail
    )
    with pytest.raises(ExecutionError, match="cannot submit"):
        Job.create(["echo"], record_id="test", project=project).submit(
            executor="hiraku-local"
        )
    job = Job.list(project=project)[0]
    assert job.status.state == psij.JobState.FAILED
    with pytest.raises(ExecutionError, match="already been submitted"):
        job.submit(executor="hiraku-local")


def test_local_launcher_startup_failure_is_persisted(project, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("launcher unavailable")

    monkeypatch.setattr(subprocess, "Popen", fail)
    job = Job.create(["echo"], record_id="test", project=project)
    with pytest.raises(ExecutionError, match="Could not submit job"):
        job.submit(executor="hiraku-local")
    assert Job.load(job.id, project=project).status.state == psij.JobState.FAILED
    with pytest.raises(ExecutionError, match="already been submitted"):
        job.submit(executor="hiraku-local")


def test_saved_nonfinal_result_is_not_inferred_from_processes(project, monkeypatch):
    job = Job.create(["echo"], record_id="test", project=project)
    job.status = psij.JobStatus(psij.JobState.QUEUED)
    job.path.parent.mkdir(parents=True)
    job._save()
    before = job.path.read_bytes()
    monkeypatch.setattr(
        psij.JobExecutor, "attach", lambda *a, **k: pytest.fail("No reattachment")
    )
    saved = Job.load(job.id, project=project)
    assert saved.status.state == psij.JobState.QUEUED
    assert job.path.read_bytes() == before


def test_detached_local_preserves_resources_and_environment(project):
    job = Job.create(
        [sys.executable, "-c", "import os; print(os.environ['CUSTOM_VALUE'])"],
        record_id="test",
        project=project,
        env={"CUSTOM_VALUE": "private-value"},
        resources=psij.ResourceSpecV1(process_count=1),
    )
    job.submit(executor="hiraku-local")
    assert job.executor.name == "hiraku-local"
    assert finish(job).exit_code == 0
    assert job.spec.stdout_path.read_text() == "private-value\n"
    assert job.spec.resources.computed_process_count == 1
    saved = Job.load(job.id, project=project)
    assert saved.spec.environment["CUSTOM_VALUE"] == "private-value"


def test_launcher_records_result_without_parent_writes(project, monkeypatch):
    gate = project.root / "finish"
    job = Job.create(
        [
            sys.executable,
            "-c",
            "import pathlib,time,sys; "
            f"p=pathlib.Path({str(gate)!r}); "
            "exec('while not p.exists(): time.sleep(.05)'); sys.exit(7)",
        ],
        record_id="test",
        project=project,
    )
    job.submit(executor="hiraku-local")
    monkeypatch.setattr(
        Job, "_save", lambda self: pytest.fail("Only the launcher writes completion")
    )
    try:
        before = job.updated_at
        saved = Job.load(job.id, project=project)
        assert saved.updated_at == before
        assert not saved.status.final
    finally:
        gate.touch()
    assert finish(job).exit_code == 7
    assert Job.load(job.id, project=project).status.final
    assert not job.status.final


def test_snapshot_roundtrip_and_collision(project, monkeypatch):
    assert Job.list(project=project) == []
    assert not (project.root / ".hiraku/jobs").exists()
    job = Job.create(
        [sys.executable, "-c", "pass"], record_id="test", project=project
    ).run()
    assert finish(job).state == psij.JobState.COMPLETED
    loaded = Job.load(job.id, project=project)
    assert loaded.model_dump(mode="json") == job.model_dump(mode="json")
    assert loaded.spec.executable == sys.executable
    assert loaded.started_at and loaded.finished_at
    monkeypatch.setattr("hiraku.jobs.uuid4", lambda: SimpleNamespace(hex=job.id))
    with pytest.raises(ExecutionError, match="already been submitted"):
        Job.create(["echo", "collision"], record_id="test", project=project).run()
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
        {"executor": 123},
        {"executor": "unknown-executor"},
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


def test_foreground_parent_crash_leaves_unknown_outcome(project):
    started = project.root / "started"
    gate = project.root / "finish"
    command = (
        "from pathlib import Path; import time; "
        f"Path({str(started)!r}).touch(); p=Path({str(gate)!r}); "
        "exec('while not p.exists(): time.sleep(.05)'); print('done', flush=True)"
    )
    parent = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from hiraku import Job, Project; "
            f"Job.create({[sys.executable, '-c', command]!r}, "
            f"record_id='test', project=Project.discover({str(project.root)!r})).run()",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    children = []
    try:
        deadline = time.monotonic() + 10
        while not started.exists():
            assert parent.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.05)
        children = psutil.Process(parent.pid).children(recursive=True)
        parent.kill()
        parent.wait(timeout=5)
        gate.touch()
        job = Job.list(project=project)[0]
        while job.spec.stdout_path.read_text() != "done\n":
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert not Job.load(job.id, project=project).status.final
        assert not job.path.with_name("result.json").exists()
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=5)
        for child in children:
            with suppress(psutil.NoSuchProcess):
                child.kill()


def test_background_survives_submitter_and_preserves_exit_code(project):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from hiraku import Job, Project; import sys; "
            "job = Job.create([sys.executable, '-c', "
            '"import time, os; time.sleep(1); '
            "print(os.environ['HIRAKU_JOB_ID']); raise SystemExit(7)\"], "
            "record_id='test', "
            f"project=Project.discover({str(project.root)!r})); "
            "job.submit(executor='hiraku-local'); print(job.id)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    job = Job.load(result.stdout.strip(), project=project)
    assert finish(job).state == psij.JobState.FAILED
    job = Job.load(job.id, project=project)
    assert job.status.exit_code == 7
    assert job.native_id and job.submitted_at and job.finished_at
    assert json.loads(job.path.with_name("result.json").read_text())["exit_code"] == 7
    assert json.loads(job.path.read_text())["status"]["state"] not in {
        "COMPLETED",
        "FAILED",
        "CANCELED",
    }
    assert job.spec.stdout_path.read_text().strip() == job.id


def test_launch_failure_is_persisted(project):
    job = Job.create(
        ["/nonexistent/hiraku-test-command"], record_id="test", project=project
    ).run()
    assert finish(job).state == psij.JobState.FAILED
    assert job.status.exit_code != 0


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
    assert job.spec.stdout_path.read_text().strip() == str(project.root)
    assert runner.invoke(main, ["job", "run"]).exit_code != 0
    assert runner.invoke(main, ["job", "submit"]).exit_code != 0
    assert runner.invoke(main, ["run", "--help"]).exit_code != 0


@pytest.mark.parametrize("options", [[], ["--executor", "hiraku-local"]])
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
    assert job.executor.name == "hiraku-local"
    with pytest.raises(ExecutionError, match="already been submitted"):
        job.submit(executor="hiraku-local")
    assert not job.status.final
    (project.root / "release").touch()
    finish(job)
    shown = runner.invoke(main, ["job", "show", job.id, "--json"])
    assert json.loads(shown.output)["status"]["state"] == "COMPLETED"
    assert "COMPLETED" in runner.invoke(main, ["job", "show", job.id]).output
    assert job.spec.stdout_path.read_text() == "hello\n"
    listed = runner.invoke(main, ["job", "list", "--json"])
    assert json.loads(listed.output)[0]["id"] == job.id
    table = runner.invoke(main, ["job", "list"], env={"COLUMNS": "140"}).output
    assert job.id in table
    assert "executor" in table
    assert job.executor.name in table


def test_interrupting_foreground_submission_cancels(project, monkeypatch):
    monkeypatch.chdir(project.root)

    original_wait = psij.Job.wait
    interrupted = False
    processes = []

    def interrupt(process, *args, **kwargs):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            processes.append(process)
            raise KeyboardInterrupt
        return original_wait(process, *args, **kwargs)

    monkeypatch.setattr(psij.Job, "wait", interrupt)
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
    assert not psutil.pid_exists(int(processes[0].native_id))
    saved = Job.list(project=project)[0]
    assert saved.status.state == psij.JobState.CANCELED
    # A canceled foreground run must not prevent subsequent execution.
    job = Job.create(
        [sys.executable, "-c", "pass"], record_id="test", project=project
    ).run()
    assert job.status.exit_code == 0


def test_environment_and_provenance_are_separate(project, monkeypatch):
    monkeypatch.setenv("HIRAKU_JOB_ID", "stale")
    job = Job.create(
        [
            sys.executable,
            "-c",
            "import os, json; print(json.dumps({k:v for k,v in os.environ.items() "
            "if k.startswith('HIRAKU_')})); print(os.environ['CUSTOM_VALUE'])",
        ],
        record_id="test",
        project=project,
        env={"CUSTOM_VALUE": "private-value"},
    ).run()
    finish(job)
    lines = job.spec.stdout_path.read_text().splitlines()
    assert json.loads(lines[0]) == {
        "HIRAKU_RECORD_ID": "test",
        "HIRAKU_JOB_ID": job.id,
    }
    assert lines[1] == "private-value"
    assert Job.load(job.id, project=project).spec.environment == job.spec.environment
    assert job.record_id == "test"


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
        Job.create(["echo", "hello"], record_id="test", project=project, env=env).run()


def test_explicit_script_runs_in_working_tree(project):
    script = project.root / "launch.sh"
    script.write_text('#!/bin/sh\n[ "$1" = "--" ] && shift\nexec "$@"\n')
    script.chmod(0o755)
    (project.root / "marker").write_text("working tree")
    job = Job.create(
        [str(script), "--", "/bin/sh", "-c", "cat marker"],
        record_id="test",
        project=project,
        directory=project.root,
    ).run()
    assert finish(job).exit_code == 0
    assert job.spec.stdout_path.read_text() == "working tree"


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
        Job.create(value, record_id="test", project=project).run()
