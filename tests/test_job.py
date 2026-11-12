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
from threading import Event
from types import SimpleNamespace

import psij
import psutil
import pytest
from click.testing import CliRunner

from hiraku.cli import main
from hiraku.errors import ExecutionError, RecordNotFoundError, ValidationError
from hiraku.job import (
    Status,
    Submission,
    prepare_job,
    run_local,
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


@pytest.mark.parametrize("relative", [False, True])
def test_foreground_retains_explicit_output(project, capsys, relative):
    job = prepare_job([sys.executable, "-c", "print('saved')"], project=project)
    output = project.root / "saved output.log"
    job.spec.directory = project.root
    path = Path(output.name) if relative else output
    job.spec.stdout_path = path
    assert run_local(job).exit_code == 0
    assert capsys.readouterr().out == "saved\n"
    assert job.spec.stdout_path == path
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
    job = prepare_job(
        [sys.executable, "-c", "print('selected')"], project=project, launcher=launcher
    )
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
    entries = Submission.list(project)
    assert len(entries) == 1
    assert entries[0].status.exit_code == code
    assert entries[0].record_id == "test"


def test_cli_submit_rejects_native_local(project, monkeypatch):
    monkeypatch.chdir(project.root)
    monkeypatch.setattr(
        "psij.executors.local.LocalJobExecutor.submit",
        lambda *args: pytest.fail("Native local job must not be submitted"),
    )
    result = CliRunner().invoke(
        main, ["job", "submit", "--executor", "local", "--", "echo", "hello"]
    )
    assert result.exit_code == 1
    assert "use hiraku run or the local-detached executor" in result.output
    assert not (project.root / ".hiraku/jobs").exists()


@pytest.mark.parametrize("command", [["run"], ["job", "submit"]])
def test_cli_launcher_and_custom_attributes_are_saved(project, monkeypatch, command):
    monkeypatch.chdir(project.root)
    result = CliRunner().invoke(
        main,
        [
            *command,
            "--launcher",
            "single",
            "--custom-attribute",
            "example",
            "value",
            "--",
            sys.executable,
            "-c",
            "pass",
        ],
    )
    assert result.exit_code == 0, result.output
    (path,) = (project.root / ".hiraku/jobs").glob("*.json")
    entry = Submission.load(project, path.stem)
    assert entry.spec["launcher"] == "single"
    assert entry.spec["attributes"]["custom_attributes"] == {"example": "value"}
    if command == ["job", "submit"]:
        job = entry.get_psi_job()
        wait(job)
        entry.sync(job)
    assert entry.status.exit_code == 0


@pytest.mark.parametrize("command", [["run"], ["job", "submit"]])
@pytest.mark.parametrize("launcher", ["not-installed", "./wrapper.sh"])
def test_cli_rejects_unregistered_launcher(project, monkeypatch, command, launcher):
    monkeypatch.chdir(project.root)
    marker = project.root / "unexpected"
    result = CliRunner().invoke(
        main,
        [
            *command,
            "--launcher",
            launcher,
            "--",
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(marker)!r}).touch()",
        ],
    )
    assert result.exit_code != 0
    assert not marker.exists()
    assert not (project.root / ".hiraku/jobs").exists()


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
    assert "observation_error" not in receipt
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
    assert shown["status"]["exit_code"] == 7
    assert shown.pop("observation_error") is None
    listed = runner.invoke(main, ["job", "list", "--record", "test", "--json"])
    assert json.loads(listed.output) == [shown]
    table = runner.invoke(main, ["job", "list"], env={"COLUMNS": "240"})
    assert "FAILED" in table.output and "test" in table.output
    assert "raise SystemExit(7)" in table.output


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
    assert Submission.load(project, job.id).status.state == "CANCELED"


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


@pytest.mark.parametrize(
    "state",
    [
        psij.JobState.NEW,
        psij.JobState.QUEUED,
        psij.JobState.ACTIVE,
        psij.JobState.COMPLETED,
        psij.JobState.FAILED,
        psij.JobState.CANCELED,
    ],
)
def test_status_preserves_native_fields(state):
    native = psij.JobStatus(
        state, time=1234, exit_code=7, message="detail", metadata={"backend": "value"}
    )
    status = Status.from_psi_status(native)
    assert status.model_dump() == {
        "state": state.name,
        "time": 1234,
        "exit_code": 7,
        "message": "detail",
        "metadata": {"backend": "value"},
    }
    assert Status.model_validate_json(status.model_dump_json()) == status


@pytest.mark.parametrize("state", ["NEW", "QUEUED", "ACTIVE", "COMPLETED"])
def test_submission_persists_any_status_snapshot(project, detached, state):
    job, _ = history_entry(project, detached)
    entry = Submission.load(project, job.id)
    status = Status(
        state=state,
        time=None if state == "COMPLETED" else 1234,
        exit_code=None,
        message=None,
        metadata=None,
    )
    entry.status = status
    entry.save(project)
    assert Submission.load(project, job.id).status == status


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
    entry = Submission.from_psi_job(job)
    entry.save(project)
    return job, Submission.load(project, job.id).model_dump(mode="json")


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
    assert entry["status"]["state"] == "QUEUED"
    monkeypatch.setattr(
        psij.JobExecutor, "get_instance", lambda *a, **k: pytest.fail("Metadata only")
    )
    assert Submission.load(project, job.id).model_dump(mode="json") == entry
    assert list((project.root / ".hiraku/jobs").glob("*.json")) == [
        project.root / ".hiraku/jobs" / f"{job.id}.json"
    ]
    assert [item.id for item in Submission.list(project)] == [job.id]


def test_submission_conversion_does_not_save(project, detached):
    job = prepare_job(["echo", "hello"], project=project)
    job.executor = detached
    job._native_id = "accepted-job"
    entry = Submission.from_psi_job(job)
    assert entry.id == job.id
    assert not (project.root / ".hiraku/jobs").exists()
    entry.save(project)
    assert Submission.load(project, job.id) == entry


def test_attachment_sync_and_save_are_explicit(project, detached, monkeypatch):
    original, _ = history_entry(project, detached)
    entry = Submission.load(project, original.id)
    path = project.root / ".hiraku/jobs" / f"{original.id}.json"
    before = path.read_bytes()

    def attach(attached, native_id):
        assert native_id == "backend-id"
        attached.executor = detached
        attached._native_id = native_id
        attached.status = psij.JobStatus(psij.JobState.ACTIVE)

    monkeypatch.setattr(detached, "attach", attach)
    monkeypatch.setattr(psij.JobExecutor, "get_instance", lambda name, url: detached)
    monkeypatch.setattr(
        psij.Job, "wait", lambda *a, **k: pytest.fail("Explicit waiting only")
    )
    attached = entry.get_psi_job()
    assert attached.id != entry.id == original.id
    assert entry.status.state == "QUEUED"
    entry.sync(attached)
    assert entry.status.state == "ACTIVE"
    assert path.read_bytes() == before
    entry.save(project)
    assert Submission.load(project, original.id).status == entry.status
    attached.status = psij.JobStatus(
        psij.JobState.FAILED, time=1234, exit_code=7, message="failed remotely"
    )
    entry.sync(attached)
    entry.save(project)
    assert Submission.load(project, original.id).status.exit_code == 7


@pytest.mark.parametrize(
    "mismatch", ["executor", "executor_url", "native_id", "unbound"]
)
def test_sync_rejects_another_execution(project, detached, mismatch):
    job, _ = history_entry(project, detached)
    entry = Submission.load(project, job.id)
    if mismatch == "native_id":
        job._native_id = "other-job"
    elif mismatch == "unbound":
        job.executor = None
    else:
        job.executor = SimpleNamespace(
            name="other" if mismatch == "executor" else detached.name,
            url="file:///other" if mismatch == "executor_url" else detached.url,
        )
    before = entry.model_dump()
    with pytest.raises(ValidationError, match="must match submission"):
        entry.sync(job)
    assert entry.model_dump() == before


@pytest.mark.parametrize("final", [False, True])
def test_status_refresh_error_preserves_snapshot_and_other_jobs(
    project, detached, monkeypatch, final
):
    monkeypatch.chdir(project.root)
    job, entry = history_entry(project, detached, final=final)
    other, completed = history_entry(project, detached, record_id=None, final=True)

    def unavailable(*args, **kwargs):
        raise psij.SubmitException("backend unavailable")

    monkeypatch.setattr(psij.JobExecutor, "get_instance", unavailable)
    response = CliRunner().invoke(main, ["job", "status", job.id, "--json"])
    assert response.exit_code == 0, response.output
    result = json.loads(response.output)
    assert result["status"] == entry["status"]
    assert "backend unavailable" in result["observation_error"]
    assert Submission.load(project, job.id).model_dump(mode="json") == entry
    entries = Submission.list(project)
    assert [item.id for item in entries] == [other.id, job.id]
    assert entries[0].status.model_dump() == completed["status"]
    assert len(Submission.list(project, record_id="test")) == 1
    listed = CliRunner().invoke(main, ["job", "list", "--json"])
    assert listed.exit_code == 0, listed.output
    assert [item["id"] for item in json.loads(listed.output)] == [other.id, job.id]


def test_record_filter_avoids_unrelated_backend_queries(project, detached, monkeypatch):
    history_entry(project, detached)
    monkeypatch.setattr(
        psij.JobExecutor, "get_instance", lambda *a, **k: pytest.fail("Unrelated job")
    )
    assert Submission.list(project, record_id="other") == []


def test_history_write_failure_reports_accepted_job(project, detached, monkeypatch):
    job = prepare_job(["echo"], project=project)
    job.executor = detached
    job._native_id = "accepted-job"

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("hiraku.job.atomic_write", fail)
    with pytest.raises(ExecutionError, match="accepted-job.*disk full"):
        Submission.from_psi_job(job).save(project)
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
    entry = Submission.load(project, submitted["id"])
    job = entry.get_psi_job()
    wait(job)
    entry.sync(job)
    entry.save(project)
    assert entry.id == submitted["id"]
    assert entry.native_id == submitted["native_id"]
    assert Submission.load(project, entry.id).status.exit_code == 7


def test_missing_or_malformed_history(project, detached):
    job = psij.Job()
    with pytest.raises(ExecutionError, match="Cannot read history"):
        Submission.load(project, job.id)
    directory = project.root / ".hiraku/jobs"
    directory.mkdir(parents=True)
    (directory / f"{job.id}.json").write_text("{")
    with pytest.raises(ExecutionError, match="Cannot read history"):
        Submission.load(project, job.id)


def test_unsubmitted_job_has_no_history(project):
    job = prepare_job(["echo"], project=project)
    with pytest.raises(ExecutionError, match="Only submitted"):
        Submission.from_psi_job(job)
    assert not (project.root / ".hiraku/jobs").exists()


@pytest.mark.parametrize("job_id", ["../outside", "unknown", "a" * 36])
def test_invalid_history_ids(project, job_id):
    with pytest.raises(ExecutionError, match="Invalid job ID"):
        Submission.load(project, job_id)


@pytest.mark.parametrize(
    "change",
    [
        {"id": "different"},
        {"id": "00000000-0000-0000-0000-000000000000"},
        {"spec": {}},
        {"executor": 3},
        {"native_id": ""},
        {"record_id": 3},
        {"executor_url": 3},
        {"spec": {"executable": "echo", "arguments": "hello"}},
        {"spec": {"executable": "echo", "arguments": [3]}},
        {
            "status": {
                "state": "UNRECOGNIZED",
                "time": 1234,
                "exit_code": None,
                "message": None,
                "metadata": None,
            }
        },
        {"status": {"state": "COMPLETED"}},
        {"submitted_at": "invalid"},
        {"submitted_at": "2026-11-12T12:00:00"},
    ],
)
def test_invalid_history_data(project, detached, change):
    job, entry = history_entry(project, detached)
    path = project.root / ".hiraku/jobs" / f"{job.id}.json"
    path.write_text(json.dumps({**entry, **change}))
    with pytest.raises(ExecutionError, match="Cannot read history"):
        Submission.load(project, job.id)


@pytest.mark.parametrize(
    "field,value",
    [
        ("state", "UNKNOWN"),
        ("time", "invalid"),
        ("exit_code", "7"),
        ("exit_code", True),
        ("message", 7),
        ("metadata", []),
    ],
)
def test_invalid_saved_status(project, detached, field, value):
    job, entry = history_entry(project, detached, final=True)
    entry["status"][field] = value
    path = project.root / ".hiraku/jobs" / f"{job.id}.json"
    path.write_text(json.dumps(entry))
    with pytest.raises(ExecutionError, match=field):
        Submission.load(project, job.id)


def test_listing_preserves_valid_jobs_with_corrupt_history(
    project, detached, monkeypatch, caplog
):
    job, _ = history_entry(project, detached, final=True)
    broken = project.root / ".hiraku/jobs" / f"{psij.Job().id}.json"
    broken.write_text("{")
    monkeypatch.setattr(
        psij.JobExecutor, "get_instance", lambda *a, **k: pytest.fail("Metadata only")
    )
    assert [entry.id for entry in Submission.list(project)] == [job.id]
    assert Submission.list(project, record_id="unrelated") == []
    assert broken.stem in caplog.text
    with pytest.raises(ExecutionError, match="Cannot read history"):
        Submission.load(project, broken.stem)


def test_saving_revalidates_mutated_submission(project, detached):
    job, _ = history_entry(project, detached)
    entry = Submission.load(project, job.id)
    path = project.root / ".hiraku/jobs" / f"{job.id}.json"
    before = path.read_bytes()
    entry.spec["arguments"] = "invalid"
    with pytest.raises(ExecutionError, match="Command arguments must be a list"):
        entry.save(project)
    assert path.read_bytes() == before


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
    submission = Submission.load(project, job.id)
    with pytest.raises(ExecutionError, match="cannot be safely reattached"):
        submission.get_psi_job()
    assert submission.status.state == "QUEUED"
    assert Submission.load(project, job.id).status == submission.status


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
