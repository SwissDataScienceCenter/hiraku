"""Native PSI/J jobs, attribution, history, and CLI behavior."""

import gc
import json
import os
import signal
import subprocess
import sys
import time
import weakref
from contextlib import suppress
from datetime import timedelta
from pathlib import Path
from threading import Event, Timer
from types import SimpleNamespace

import psij
import psutil
import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.errors import ExecutionError, RecordNotFoundError, ValidationError
from hiraku.job import (
    describe_job,
    list_submissions,
    load_submission,
    observe_submission,
    prepare_job,
    run_local,
    save_submission,
    validate_command,
)
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
        "from hiraku.job import prepare_job,run_local; "
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
    assert job.spec.launcher == "quoted-single"
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


def test_detached_rejects_multiple_processes(project, detached):
    job = prepare_job(
        ["echo"], project=project, resources=psij.ResourceSpecV1(process_count=2)
    )
    with pytest.raises(psij.InvalidJobException, match="single local process"):
        detached.submit(job)
    assert job.executor is None and job.native_id is None


@pytest.mark.parametrize("launcher", ["quoted-single", "single"])
def test_detached_uses_selected_launcher(project, detached, launcher):
    job = prepare_job([sys.executable, "-c", "print('selected')"], project=project)
    job.spec.launcher = launcher
    detached.submit(job)
    assert wait(job).exit_code == 0
    assert job.spec.launcher == launcher
    assert job.spec.stdout_path.read_text() == "selected\n"


def test_detached_environment_and_directory(project, detached, monkeypatch):
    monkeypatch.setenv("PARENT_ONLY", "inherited")
    command = (
        "import os; print(os.getcwd()); print(os.environ['COUNT']); "
        "print(repr(os.environ.get('PARENT_ONLY')))"
    )
    job = psij.Job(
        psij.JobSpec(
            sys.executable,
            ["-c", command],
            directory=project.root,
            inherit_environment=False,
            environment={"COUNT": 42},
        )
    )
    detached.submit(job)
    assert wait(job).exit_code == 0
    assert job.spec.stdout_path.read_text().splitlines() == [
        str(project.root),
        "42",
        "None",
    ]


def test_detachment_and_child_reaping(project, detached, monkeypatch):
    original = subprocess.Popen
    processes = []

    def popen(*args, **kwargs):
        assert kwargs["start_new_session"] is True
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)
    job = prepare_job(
        [sys.executable, "-c", "import os; print(os.getsid(0))"], project=project
    )
    detached.submit(job)
    assert wait(job).exit_code == 0
    process = processes[0]
    assert int(job.spec.stdout_path.read_text()) == process.pid
    deadline = time.monotonic() + 5
    while process.returncode is None:
        assert time.monotonic() < deadline, "Supervisor was not reaped"
        time.sleep(0.01)


def test_discarded_submission_is_reaped_without_retaining_job(
    project, detached, monkeypatch
):
    original = subprocess.Popen
    processes = []
    gate = project.root / "release-discarded"

    def popen(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)
    command = (
        "from pathlib import Path; import time; "
        f"p=Path({str(gate)!r}); "
        "exec('while not p.exists(): time.sleep(.01)')"
    )
    job = prepare_job([sys.executable, "-c", command], project=project)
    detached.submit(job)
    reference = weakref.ref(job)
    del job
    try:
        deadline = time.monotonic() + 5
        while reference() is not None:
            gc.collect()
            assert time.monotonic() < deadline, "Observer retained discarded job"
            time.sleep(0.01)
    finally:
        gate.touch()
    while processes[0].returncode is None:
        assert time.monotonic() < deadline, "Discarded supervisor was not reaped"
        time.sleep(0.01)


def test_discarded_attachment_does_not_keep_observer_alive(project, detached):
    attached = psij.Job()
    (detached.jobs_dir / attached.id).mkdir(parents=True)
    reference = weakref.ref(attached)
    detached.attach(attached, attached.id)
    del attached
    deadline = time.monotonic() + 5
    while reference() is not None:
        gc.collect()
        assert time.monotonic() < deadline, "Observer retained discarded attachment"
        time.sleep(0.01)


def test_supervisor_start_failure_does_not_bind_job(project, detached, monkeypatch):
    original = subprocess.Popen

    def fail(*args, **kwargs):
        raise OSError("cannot start")

    monkeypatch.setattr(subprocess, "Popen", fail)
    job = prepare_job(["echo"], project=project)
    with pytest.raises(
        psij.SubmitException, match="Could not start detached supervisor"
    ):
        detached.submit(job)
    assert job.status.state == psij.JobState.NEW
    assert job.executor is None and job.native_id is None
    monkeypatch.setattr(subprocess, "Popen", original)
    detached.submit(job)
    assert wait(job).exit_code == 0


def test_supervisor_records_launcher_start_failure(project, detached, monkeypatch):
    monkeypatch.setattr(
        detached,
        "_get_launcher",
        lambda name: SimpleNamespace(
            get_launch_command=lambda job: ["/nonexistent/hiraku-test-launcher"],
        ),
    )
    job = prepare_job(["echo"], project=project)
    detached.submit(job)
    status = wait(job)
    assert status.state == psij.JobState.FAILED
    assert status.exit_code is None
    assert "hiraku-test-launcher" in status.message
    attached = psij.Job()
    detached.attach(attached, job.native_id)
    assert wait(attached).message == status.message


def test_supervisor_preserves_native_signal_exit(project, detached, monkeypatch):
    monkeypatch.setattr(
        detached,
        "_get_launcher",
        lambda name: SimpleNamespace(
            get_launch_command=lambda job: [
                sys.executable,
                "-c",
                "import os,signal; os.kill(os.getpid(),signal.SIGTERM)",
            ],
        ),
    )
    job = prepare_job(["echo"], project=project)
    detached.submit(job)
    assert wait(job).exit_code == -15
    attached = psij.Job()
    detached.attach(attached, job.native_id)
    assert wait(attached).exit_code == -15


def test_killed_supervisor_leaves_unknown_outcome(project, detached):
    marker = project.root / "supervisor-session"
    command = (
        "import os,time; from pathlib import Path; "
        f"Path({str(marker)!r}).write_text(str(os.getsid(0))); time.sleep(30)"
    )
    job = prepare_job([sys.executable, "-c", command], project=project)
    detached.submit(job)
    try:
        deadline = time.monotonic() + 10
        while not marker.exists():
            assert time.monotonic() < deadline, "Command did not start"
            time.sleep(0.01)
        os.killpg(int(marker.read_text()), signal.SIGKILL)
        assert job.wait(timeout=timedelta(seconds=0.2)) is None
        attached = psij.Job()
        detached.attach(attached, job.native_id)
        assert attached.wait(timeout=timedelta(seconds=0.2)) is None
        assert not (detached.jobs_dir / job.native_id / "result.json").exists()
    finally:
        if marker.exists():
            with suppress(ProcessLookupError):
                os.killpg(int(marker.read_text()), signal.SIGKILL)


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
    # An interrupted supervisor may leave only its backend directory.
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
    entries = list_submissions(project=project)
    assert len(entries) == 1
    assert entries[0]["final_status"]["exit_code"] == code
    assert entries[0]["record_id"] == "test"


def test_cli_submit_list_and_status(project, detached, monkeypatch):
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
    args = [
        "job",
        "status",
        receipt["id"],
        "--wait",
        "--json",
    ]
    status = runner.invoke(main, args)
    assert status.exit_code == 0, status.output
    assert json.loads(status.output)["status"]["exit_code"] == 7
    assert "FAILED" in runner.invoke(main, args[:-1]).output
    shown = json.loads(status.output)
    assert shown["id"] == receipt["id"]
    assert shown["final_status"]["exit_code"] == 7
    listed = runner.invoke(main, ["job", "list", "--record", "test", "--json"])
    assert json.loads(listed.output) == [shown]
    table = runner.invoke(main, ["job", "list"], env={"COLUMNS": "240"})
    assert "FAILED" in table.output and "test" in table.output
    assert "raise SystemExit(7)" in table.output
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
        run_local(job, project=project)
    assert job.status.state == psij.JobState.CANCELED
    assert not psutil.pid_exists(int(job.native_id))
    assert (
        load_submission(job.id, project=project)["final_status"]["state"] == "CANCELED"
    )


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
    status = describe_job(job)["status"]
    assert status["state"] == "FAILED" and status["exit_code"] == 7
    assert status["time"] == job.status.time


def history_entry(project, detached, *, record_id="test", final=False):
    job = prepare_job(
        [sys.executable, "train.py", "argument with spaces"],
        project=project,
        record_id=record_id,
        attributes=psij.JobAttributes(custom_attributes={"example": "value"}),
        resources=psij.ResourceSpecV1(process_count=1),
    )
    job.spec.launcher = "quoted-single"
    job.executor = detached
    job._native_id = "backend-id"
    job.status = psij.JobStatus(
        psij.JobState.FAILED if final else psij.JobState.QUEUED,
        exit_code=7 if final else None,
    )
    result = save_submission(job, project=project)
    assert result["status"]["state"] == job.status.state.name
    return job, load_submission(job.id, project=project)


def test_history_uses_original_psij_id_and_native_spec(project, detached, monkeypatch):
    job, entry = history_entry(project, detached)
    assert entry["id"] == job.id
    assert entry["native_id"] == "backend-id"
    assert entry["record_id"] == "test"
    assert entry["spec"]["arguments"] == ["train.py", "argument with spaces"]
    assert entry["spec"]["launcher"] == "quoted-single"
    assert entry["spec"]["resources"]["process_count"] == 1
    assert entry["spec"]["attributes"]["custom_attributes"] == {"example": "value"}
    assert entry["spec"]["environment"]["HIRAKU_JOB_ID"] == job.id
    assert entry["final_status"] is None
    monkeypatch.setattr(
        psij.JobExecutor, "get_instance", lambda *a, **k: pytest.fail("Metadata only")
    )
    assert load_submission(job.id, project=project) == entry
    assert list((project.root / ".hiraku/jobs").glob("*.json")) == [
        project.root / ".hiraku/jobs" / f"{job.id}.json"
    ]


def test_observation_attaches_and_persists_final_status(project, detached, monkeypatch):
    job, entry = history_entry(project, detached)
    attached_ids = []

    def attach(attached, native_id):
        assert native_id == "backend-id"
        attached_ids.append(attached.id)
        attached.status = psij.JobStatus(psij.JobState.ACTIVE)
        Timer(
            0.05,
            lambda: setattr(
                attached,
                "status",
                psij.JobStatus(
                    psij.JobState.FAILED,
                    time=1234,
                    exit_code=7,
                    message="failed remotely",
                    metadata={"backend": "detail"},
                ),
            ),
        ).start()

    monkeypatch.setattr(
        psij.JobExecutor,
        "get_instance",
        lambda name, url: SimpleNamespace(attach=attach),
    )
    result = observe_submission(job.id, project=project, wait=True)
    assert attached_ids[0] != job.id
    assert result["id"] == job.id
    assert result["status"] == result["final_status"]
    assert result["status"] == {
        "state": "FAILED",
        "time": 1234,
        "exit_code": 7,
        "message": "failed remotely",
        "metadata": {"backend": "detail"},
    }
    assert load_submission(job.id, project=project)["final_status"] == result["status"]
    monkeypatch.setattr(
        psij.JobExecutor, "get_instance", lambda *a, **k: pytest.fail("Saved outcome")
    )
    assert observe_submission(job.id, project=project) == result
    assert list_submissions(project=project) == [result]


def test_pending_observation_is_bounded_and_not_persisted(
    project, detached, monkeypatch
):
    job, entry = history_entry(project, detached)

    def attach(attached, native_id):
        # An executor may return before its first status notification.
        pass

    monkeypatch.setattr(
        psij.JobExecutor, "get_instance", lambda *a, **k: SimpleNamespace(attach=attach)
    )
    result = observe_submission(job.id, project=project)
    assert result["status"]["state"] == "NEW"
    assert load_submission(job.id, project=project) == entry


def test_observation_error_preserves_history_and_other_jobs(
    project, detached, monkeypatch
):
    job, entry = history_entry(project, detached)
    other, completed = history_entry(project, detached, record_id=None, final=True)

    def unavailable(*args, **kwargs):
        raise psij.SubmitException("backend unavailable")

    monkeypatch.setattr(psij.JobExecutor, "get_instance", unavailable)
    result = observe_submission(job.id, project=project)
    assert result["status"]["state"] == "UNKNOWN"
    assert result["observation_error"] == "backend unavailable"
    assert result["final_status"] is None
    assert load_submission(job.id, project=project) == entry
    entries = list_submissions(project=project)
    assert [item["id"] for item in entries] == [other.id, job.id]
    assert entries[0]["status"] == completed["final_status"]
    assert len(list_submissions(project=project, record_id="test")) == 1


def test_record_filter_avoids_unrelated_backend_queries(project, detached, monkeypatch):
    history_entry(project, detached)
    monkeypatch.setattr(
        psij.JobExecutor, "get_instance", lambda *a, **k: pytest.fail("Unrelated job")
    )
    assert list_submissions(project=project, record_id="other") == []


def test_history_write_failure_reports_accepted_job(project, detached, monkeypatch):
    job = prepare_job(["echo"], project=project)
    job.executor = detached
    job._native_id = "accepted-job"

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("hiraku.job.atomic_write", fail)
    with pytest.raises(ExecutionError, match="accepted-job.*disk full"):
        save_submission(job, project=project)
    assert job.native_id == "accepted-job"


def test_history_survives_submitting_process(project, detached):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from hiraku.cli import main; main()",
            "job",
            "submit",
            "--executor-url",
            detached.url,
            "--",
            sys.executable,
            "-c",
            "import time; time.sleep(.2); raise SystemExit(7)",
        ],
        cwd=project.root,
        text=True,
        capture_output=True,
        check=True,
    )
    submitted = json.loads(result.stdout)
    observed = observe_submission(submitted["id"], project=project, wait=True)
    assert observed["id"] == submitted["id"]
    assert observed["native_id"] == submitted["native_id"]
    assert observed["final_status"]["exit_code"] == 7
    assert (
        load_submission(submitted["id"], project=project)["final_status"]
        == (observed["final_status"])
    )


def test_missing_or_malformed_history(project, detached):
    job = psij.Job()
    with pytest.raises(ExecutionError, match="Cannot read history"):
        load_submission(job.id, project=project)
    directory = project.root / ".hiraku/jobs"
    directory.mkdir(parents=True)
    (directory / f"{job.id}.json").write_text("{")
    with pytest.raises(ExecutionError, match="Cannot read history"):
        load_submission(job.id, project=project)


def test_unsubmitted_job_has_no_history(project):
    job = prepare_job(["echo"], project=project)
    with pytest.raises(ExecutionError, match="Only submitted"):
        save_submission(job, project=project)
    assert not (project.root / ".hiraku/jobs").exists()


@pytest.mark.parametrize("job_id", ["../outside", "unknown", "a" * 36])
def test_invalid_history_ids(project, job_id):
    with pytest.raises(ExecutionError, match="Invalid job ID"):
        load_submission(job_id, project=project)


@pytest.mark.parametrize(
    "change",
    [
        {"id": "different"},
        {"spec": {}},
        {"executor": 3},
        {"final_status": {"state": "ACTIVE"}},
        {"submitted_at": "invalid"},
    ],
)
def test_invalid_history_data(project, detached, change):
    job, entry = history_entry(project, detached)
    path = project.root / ".hiraku/jobs" / f"{job.id}.json"
    path.write_text(json.dumps({**entry, **change}))
    with pytest.raises(ExecutionError, match="Cannot read history"):
        load_submission(job.id, project=project)


def test_foreground_unknown_outcome_does_not_attach_to_pid(
    project, detached, monkeypatch
):
    job, entry = history_entry(project, detached)
    path = project.root / ".hiraku/jobs" / f"{job.id}.json"
    entry["executor"] = "local"
    path.write_text(json.dumps(entry))
    monkeypatch.setattr(
        psij.JobExecutor,
        "get_instance",
        lambda *a, **k: pytest.fail("Unsafe PID lookup"),
    )
    result = observe_submission(job.id, project=project)
    assert result["status"]["state"] == "UNKNOWN"
    assert "foreground" in result["observation_error"]
    assert load_submission(job.id, project=project)["final_status"] is None


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
