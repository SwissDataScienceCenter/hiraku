"""Native PSI/J jobs, attribution, history, and CLI behavior."""

import gc
import io
import json
import os
import signal
import subprocess
import sys
import time
import weakref
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import psij
import psutil
import pytest
from click.testing import CliRunner
from psij.executors.batch.batch_scheduler_executor import BatchSchedulerExecutor

from hiraku.cli import main
from hiraku.errors import (
    ExecutionError,
    ObservationError,
    RecordNotFoundError,
    ValidationError,
)
from hiraku.job import (
    Status,
    Submission,
    get_executor,
    observe_submission,
    prepare_job,
    run_local,
    submit_job,
    validate_command,
    wait_for_submissions,
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
    project.vault.create("test", title="Test", type="task")
    return project


@pytest.fixture
def detached(project):
    return psij.JobExecutor.get_instance("local-detached")


def test_foreground_launcher_storage(project):
    job = prepare_job([sys.executable, "-c", "pass"], project=project)
    assert run_local(job, project=project).exit_code == 0
    work = job.executor.config.work_directory
    assert work == project.psij_dir
    assert list(work.glob("*/single.sh"))
    assert not psij.JobExecutorConfig.DEFAULT_WORK_DIRECTORY.exists()


@pytest.mark.parametrize("custom_url", [False, True])
def test_project_executor_storage(project, custom_url):
    backend = project.root / "custom backend"
    executor = get_executor(
        "local-detached",
        work_directory=project.psij_dir,
        url=backend.as_uri() if custom_url else None,
    )
    work = project.root / ".hiraku/psij"
    assert executor.config.work_directory == work
    expected = backend if custom_url else work / "detached"
    assert executor.jobs_dir == expected
    job = prepare_job([sys.executable, "-c", "print('done')"], project=project)
    executor.submit(job)
    assert wait(job).exit_code == 0
    assert (expected / job.id / "result.json").is_file()
    assert list(work.glob("*/single.sh"))
    entry = Submission.from_psi_job(job)
    entry.save(project)
    loaded = Submission.load(project, job.id)
    assert loaded.executor == "local-detached"
    assert loaded.executor_url == executor.url
    assert loaded.executor_work_directory == str(work)
    attached = loaded.get_psi_job()
    assert attached.executor.config.work_directory == work
    assert attached.executor.url == executor.url
    assert attached.status.exit_code == 0
    assert not psij.JobExecutorConfig.DEFAULT_WORK_DIRECTORY.exists()


def wait(job):
    status = job.wait(timeout=timedelta(seconds=15))
    assert status is not None, "Job did not finish"
    return status


def test_loading_history_does_not_construct_executor(project, detached, monkeypatch):
    job, saved = history_entry(project, detached)
    monkeypatch.setattr(
        psij.JobExecutor,
        "get_instance",
        lambda *a, **k: pytest.fail("Loading history must not construct an executor"),
    )
    loaded = Submission.load(project, job.id)
    assert loaded.model_dump(mode="json") == saved


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
    assert run_local(job, project=project).exit_code == 0
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
    assert run_local(job, project=project).exit_code == 0
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
    assert run_local(job, project=project).exit_code == 0
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
    assert run_local(job, project=project).exit_code == code
    output = capsys.readouterr()
    assert output.out == "héllo\n" and output.err == "error\n"
    assert job.spec.stdout_path is None and job.spec.stderr_path is None
    submission = Submission.load(project, job.id)
    assert submission.status.exit_code == code
    assert submission.executor_work_directory == str(project.psij_dir)
    with pytest.raises(psij.InvalidJobException):
        run_local(job, project=project)


def test_foreground_streams_before_completion(project):
    command = (
        "import sys; from hiraku.project import Project; "
        "from hiraku.job import prepare_job,run_local; "
        f"project=Project.discover({str(project.root)!r}); "
        "run_local(prepare_job([sys.executable,'-c',"
        "'import time; print(\"ready\",flush=True); time.sleep(1)'],project=project),"
        "project=project)"
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


def test_foreground_rejects_running_job_without_mutation(project):
    release = project.root / "release"
    job = prepare_job(
        [
            sys.executable,
            "-c",
            "import time; from pathlib import Path\n"
            f"while not Path({str(release)!r}).exists(): time.sleep(.05)",
        ],
        project=project,
    )
    psij.JobExecutor.get_instance("local").submit(job)
    original = job.spec.__dict__.copy()
    try:
        with pytest.raises(psij.InvalidJobException, match="new and unbound"):
            run_local(job, project=project)
        assert job.spec.__dict__ == original
        assert not job.status.final
    finally:
        release.touch()
        status = wait(job)
    assert status.state == psij.JobState.COMPLETED


@pytest.mark.parametrize("binary", [False, True])
def test_foreground_binary_and_text_streams(project, monkeypatch, binary):
    stdout = (
        io.TextIOWrapper(io.BytesIO(), encoding="utf-8") if binary else io.StringIO()
    )
    stderr = (
        io.TextIOWrapper(io.BytesIO(), encoding="utf-8") if binary else io.StringIO()
    )
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    payload = b"\x00\x8b\xffh\xc3\xa9"
    job = prepare_job(
        [
            sys.executable,
            "-c",
            f"import os; os.write(1, {payload!r}); os.write(2, {payload!r})",
        ],
        project=project,
    )
    assert run_local(job, project=project).exit_code == 0
    for output in (stdout, stderr):
        if binary:
            assert output.buffer.getvalue() == payload
        else:
            assert output.getvalue() == payload.decode("utf-8", errors="replace")


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
        status = run_local(job, project=project)
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
        identity = detached._result_path(job.native_id).with_name("supervisor.json")
        deadline = time.monotonic() + 5
        while not identity.is_file():
            assert time.monotonic() < deadline, "Supervisor did not become ready"
            time.sleep(0.01)
        detached.attach(attached, job.native_id)
        assert attached.status.state == psij.JobState.ACTIVE
        entry = Submission.from_psi_job(job)
        entry.sync()
        assert entry.status.state == "ACTIVE"
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


@pytest.mark.parametrize("block_result", [False, True])
def test_supervisor_identity_failure_prevents_execution(
    project, detached, monkeypatch, block_result
):
    marker = project.root / "executed"
    job = prepare_job(
        [
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(marker)!r}).touch()",
        ],
        project=project,
    )
    path = detached._result_path(job.id)
    path.parent.mkdir(parents=True)
    if block_result:
        path.mkdir()
    else:
        path.with_name("supervisor.json").mkdir()
    processes = []
    popen = subprocess.Popen

    def capture(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", capture)
    if block_result:
        with pytest.raises(
            psij.InvalidJobException, match="Detached job result path already exists"
        ):
            detached.submit(job)
        assert not processes
    else:
        with pytest.raises(
            psij.SubmitException, match="Could not start detached supervisor"
        ):
            detached.submit(job)
        assert len(processes) == 1 and processes[0].returncode is not None

    assert job.status.state == psij.JobState.NEW
    assert job.executor is None and job.native_id is None
    assert not marker.exists()


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


def test_killed_supervisor_reports_lost_outcome(project, detached):
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
        status = wait(job)
        assert status.state == psij.JobState.FAILED
        assert status.exit_code is None
        assert "Supervisor exited" in status.message
        attached = psij.Job()
        detached.attach(attached, job.native_id)
        assert attached.status.state == psij.JobState.FAILED
        assert attached.status.exit_code is None
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
        "import psij,sys; from pathlib import Path; "
        "from hiraku.job import get_executor; "
        "e=get_executor('local-detached',"
        f"url={detached.url!r},"
        f"work_directory=Path({str(project.psij_dir)!r})); "
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
    assert list(project.psij_dir.glob("*/single.sh"))


def test_detached_startup_waits_for_supervisor_identity(project, detached):
    # Attachment can race with the supervisor's first write.
    job = psij.Job()
    (detached.jobs_dir / job.id).mkdir(parents=True)
    detached.attach(job, job.id)
    assert job.wait(timeout=timedelta(seconds=0.2)) is None
    assert not job.status.final


@pytest.mark.parametrize("condition", ["reused", "missing", "zombie", "denied"])
def test_detached_supervisor_identity(detached, monkeypatch, condition):
    job = psij.Job()
    path = detached.jobs_dir / job.id / "result.json"
    path.parent.mkdir(parents=True)
    path.with_name("supervisor.json").write_text(
        json.dumps({"pid": 12345, "create_time": 1.0})
    )

    def process(pid):
        assert pid == 12345
        if condition == "missing":
            raise psutil.NoSuchProcess(pid)
        if condition == "denied":
            raise psutil.AccessDenied(pid)
        return SimpleNamespace(
            create_time=lambda: 2.0 if condition == "reused" else 1.0,
            status=lambda: (
                psutil.STATUS_ZOMBIE if condition == "zombie" else psutil.STATUS_RUNNING
            ),
        )

    monkeypatch.setattr(psutil, "Process", process)
    if condition == "denied":
        with pytest.raises(OSError, match="Cannot inspect"):
            detached.attach(job, job.id)
        assert job.executor is None
    else:
        detached.attach(job, job.id)
        assert job.status.state == psij.JobState.FAILED
        assert job.status.exit_code is None


def test_detached_completion_races_with_supervisor_exit(detached, monkeypatch):
    job = psij.Job()
    path = detached.jobs_dir / job.id / "result.json"
    path.parent.mkdir(parents=True)
    path.with_name("supervisor.json").write_text(
        json.dumps({"pid": 12345, "create_time": 1.0})
    )

    def process(pid):
        path.write_text(
            json.dumps({"exit_code": 0, "finished_at": datetime.now(UTC).isoformat()})
        )
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(psutil, "Process", process)
    detached.attach(job, job.id)
    assert job.status.state == psij.JobState.COMPLETED
    assert job.status.exit_code == 0


@pytest.mark.parametrize("code", [0, 4])
def test_cli_foreground(project, monkeypatch, code):
    monkeypatch.chdir(project.root)
    result = CliRunner().invoke(
        main,
        [
            "run",
            "--record",
            "test",
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


def test_slurm_submission_keeps_logs_and_exit_code(project, monkeypatch):
    project = Project.create(
        project.root.parent / "slurm-project", install_skill=False, install_mcp=False
    )
    monkeypatch.chdir(project.root)
    bin_dir = project.root / "bin"
    bin_dir.mkdir()
    scontrol = bin_dir / "scontrol"
    scontrol.write_text("#!/bin/sh\necho node1\n", encoding="utf-8")
    scontrol.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir), prepend=os.pathsep)
    monkeypatch.setenv("SLURM_JOB_ID", "42")
    monkeypatch.delenv("PSIJ_BATCH_KEEP_FILES", raising=False)
    monkeypatch.setattr(
        BatchSchedulerExecutor,
        "_start_queue_poll_thread",
        lambda self: SimpleNamespace(register_job=lambda job: None),
    )

    def run_batch_script(self, command):
        assert self.config.keep_files
        assert command[0] == "sbatch"
        subprocess.run(["bash", command[1]], cwd=project.root, check=True)
        return "Submitted batch job 42"

    monkeypatch.setattr(BatchSchedulerExecutor, "_run_command", run_batch_script)
    cli = CliRunner()
    submitted = cli.invoke(
        main,
        [
            "job",
            "submit",
            "--executor",
            "slurm",
            "--",
            sys.executable,
            "-c",
            "import sys; print('out'); print('err', file=sys.stderr); sys.exit(13)",
        ],
        catch_exceptions=False,
    )
    assert submitted.exit_code == 0, submitted.output
    receipt = json.loads(submitted.stdout)
    job_id = receipt["id"]
    work = project.psij_dir / "slurm"
    assert receipt["spec"]["stdout_path"] == str(work / f"{job_id}.stdout")
    assert receipt["spec"]["stderr_path"] == str(work / f"{job_id}.stderr")
    assert receipt["spec"]["launcher"] == "quoted-single"
    logs = cli.invoke(main, ["job", "logs", job_id, "--json"])
    assert logs.exit_code == 0, logs.output
    assert json.loads(logs.stdout) == {"stdout": "out\n", "stderr": "err\n"}

    def finish_observation(job, *, timeout, target_states):
        assert job.status.state == psij.JobState.NEW
        assert timeout == timedelta(seconds=8)
        assert target_states == psij.JobState.QUEUED
        assert job.executor.config.keep_files
        job.executor._set_job_status(job, psij.JobStatus(psij.JobState.COMPLETED))
        return job.status

    status_args = ["job", "status", job_id, "--json"]
    stale = Submission.load(project, job_id)
    monkeypatch.setattr(psij.Job, "wait", lambda *args, **kwargs: None)
    timed_out = cli.invoke(main, status_args)
    assert timed_out.exit_code == 0, timed_out.output
    assert json.loads(timed_out.stdout) == receipt
    assert Submission.load(project, job_id) == stale

    monkeypatch.setattr(psij.Job, "wait", finish_observation)
    observed = cli.invoke(main, status_args)
    assert observed.exit_code == 0, observed.output
    final = json.loads(observed.stdout)
    assert final["status"]["state"] == "FAILED"
    assert final["status"]["exit_code"] == 13
    assert (work / "42.ec").read_text(encoding="utf-8").strip() == "13"

    stale.save(project)
    assert stale.status.exit_code == 13
    assert Submission.load(project, job_id).model_dump(mode="json") == final
    monkeypatch.setattr(
        BatchSchedulerExecutor,
        "attach",
        lambda *args: pytest.fail("Final job reattached"),
    )
    cached = cli.invoke(main, status_args)
    assert cached.exit_code == 0, cached.output
    assert json.loads(cached.stdout) == final


def test_slurm_submission_preserves_explicit_paths_and_launcher(project, monkeypatch):
    def submit(self, job):
        job.executor = self
        job._native_id = "42"
        job.status = psij.JobStatus(psij.JobState.QUEUED)

    monkeypatch.setattr(BatchSchedulerExecutor, "submit", submit)
    job = prepare_job(["echo", "hello"], project=project, launcher="srun")
    job.spec.stdout_path = Path("stdout.log")
    job.spec.stderr_path = Path("stderr.log")
    entry = submit_job(job, project=project, executor="slurm")
    assert entry.spec["launcher"] == "srun"
    assert entry.spec["stdout_path"] == "stdout.log"
    assert entry.spec["stderr_path"] == "stderr.log"


@pytest.mark.parametrize("command", [["run"], ["job", "submit"]])
def test_cli_launcher_and_custom_attributes_are_saved(project, monkeypatch, command):
    # PSI/J's native single cannot source its library from a spaced path.
    project = Project.create(
        project.root.parent / "native-launcher",
        install_skill=False,
        install_mcp=False,
    )
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


@pytest.mark.parametrize("custom_url", [False, True])
def test_cli_submit_status_and_list(project, detached, monkeypatch, custom_url):
    monkeypatch.chdir(project.root)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "job",
            "submit",
            *(["--executor-url", detached.url] if custom_url else []),
            "--record",
            "test",
            "--",
            sys.executable,
            "-c",
            "raise SystemExit(7)",
        ],
    )
    assert result.exit_code == 0, result.output
    receipt = json.loads(result.output)
    expected_url = (
        detached.url
        if custom_url
        else (project.root / ".hiraku/psij/detached").as_uri()
    )
    assert receipt["executor_url"] == expected_url
    assert list((project.root / ".hiraku/psij").glob("*/single.sh"))
    assert "observation_error" not in receipt
    wait(Submission.load(project, receipt["id"]).get_psi_job())
    args = [
        "job",
        "status",
        receipt["id"][:8],
        "--json",
    ]
    status = runner.invoke(main, args)
    assert status.exit_code == 0, status.output
    assert json.loads(status.output)["status"]["exit_code"] == 7
    shown = json.loads(status.output)
    assert shown["id"] == receipt["id"]
    assert shown["status"]["exit_code"] == 7
    assert "FAILED" in runner.invoke(main, ["job", "status", receipt["id"]]).output
    shown = Submission.load(project, receipt["id"]).model_dump(mode="json")
    listed = runner.invoke(main, ["job", "list", "--record", "test", "--json"])
    assert json.loads(listed.output) == [shown]
    table = runner.invoke(main, ["job", "list"], env={"COLUMNS": "320"})
    assert "FAILED" in table.output
    assert "state" in table.output
    assert "test" in table.output
    assert "raise SystemExit(7)" in table.output
    assert receipt["id"][:8] in table.output
    assert receipt["id"] not in table.output
    submitted = datetime.fromisoformat(receipt["submitted_at"]).astimezone()
    assert submitted.strftime("%Y-%m-%d %H:%M:%S") in table.output


def _submit_detached(runner, code):
    result = runner.invoke(
        main, ["job", "submit", "--record", "test", "--", sys.executable, "-c", code]
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["id"]


def test_cli_wait_returns_final_status_and_exit_code(project, detached, monkeypatch):
    monkeypatch.chdir(project.root)
    runner = CliRunner()
    job_id = _submit_detached(runner, "import time; time.sleep(1); raise SystemExit(7)")
    waited = runner.invoke(main, ["job", "wait", job_id[:8], "--json"])
    assert waited.exit_code == 1, waited.output
    [shown] = json.loads(waited.output)
    assert shown["id"] == job_id
    assert shown["status"]["state"] == "FAILED"
    assert shown["status"]["exit_code"] == 7
    assert Submission.load(project, job_id).model_dump(mode="json") == shown

    # A final job is answered from history without reattaching.
    monkeypatch.setattr(
        Submission, "get_psi_job", lambda self: pytest.fail("Final job reattached")
    )
    again = runner.invoke(main, ["job", "wait", job_id])
    assert again.exit_code == 1, again.output
    assert "FAILED" in again.output


def test_cli_wait_for_several_jobs(project, detached, monkeypatch):
    monkeypatch.chdir(project.root)
    runner = CliRunner()
    first = _submit_detached(runner, "pass")
    second = _submit_detached(runner, "import time; time.sleep(1)")
    waited = runner.invoke(main, ["job", "wait", first, second, "--json"])
    assert waited.exit_code == 0, waited.output
    shown = json.loads(waited.output)
    assert [item["id"] for item in shown] == [first, second]
    assert {item["status"]["state"] for item in shown} == {"COMPLETED"}


def test_cli_wait_any_and_timeout(project, detached, monkeypatch):
    monkeypatch.chdir(project.root)
    runner = CliRunner()
    slow = _submit_detached(runner, "import time; time.sleep(60)")
    fast = _submit_detached(runner, "pass")
    try:
        started = time.monotonic()
        timed_out = runner.invoke(main, ["job", "wait", slow, "--timeout", "1s"])
        assert timed_out.exit_code == 124, timed_out.output
        assert time.monotonic() - started < 30
        assert not Submission.load(project, slow).status.final

        either = runner.invoke(main, ["job", "wait", slow, fast, "--any", "--json"])
        assert either.exit_code == 0, either.output
        states = {
            item["id"]: item["status"]["state"] for item in json.loads(either.output)
        }
        assert states[fast] == "COMPLETED"
        assert states[slow] not in {"COMPLETED", "FAILED", "CANCELED"}
    finally:
        runner.invoke(main, ["job", "cancel", slow])


@pytest.mark.parametrize("timeout", ["soon", "-1", "1d", "inf"])
def test_cli_wait_rejects_invalid_timeout(project, timeout):
    result = CliRunner().invoke(
        main, ["job", "wait", "0", "--timeout", timeout], catch_exceptions=False
    )
    assert result.exit_code == 2
    assert "--timeout" in result.output


def test_wait_rejects_repeated_jobs_and_negative_timeout(
    project, detached, monkeypatch
):
    monkeypatch.chdir(project.root)
    job = prepare_job([sys.executable, "-c", "pass"], project=project)
    entry = submit_job(job, project=project, executor="local-detached")
    result = CliRunner().invoke(main, ["job", "wait", entry.id, entry.id[:8]])
    assert result.exit_code == 1
    assert "only once" in result.output
    with pytest.raises(ValidationError, match="negative"):
        wait_for_submissions([entry], project=project, timeout=timedelta(seconds=-1))


def test_wait_deadline_never_passes_a_zero_timeout(project, detached, monkeypatch):
    # A sub-microsecond remainder rounds to timedelta(0), which PSI/J's
    # Job.wait() treats as "wait without a timeout".
    job = prepare_job([sys.executable, "-c", "pass"], project=project)
    entry = submit_job(job, project=project, executor="local-detached")
    entry.status = Status(
        state="QUEUED", time=0.0, exit_code=None, message=None, metadata=None
    )
    timeouts = []

    class Pending:
        def wait(self, *, timeout):
            assert timeout > timedelta(0), "a zero timeout would never return"
            timeouts.append(timeout)

    clock = iter([100.0, 101.0 - 4e-7])
    monkeypatch.setattr(
        "hiraku.job.time", SimpleNamespace(monotonic=lambda: next(clock))
    )
    monkeypatch.setattr(Submission, "get_psi_job", lambda self: Pending())
    monkeypatch.setattr(Submission, "sync", lambda self, job=None: None)
    monkeypatch.setattr(Submission, "save", lambda self, project: None)
    met = wait_for_submissions([entry], project=project, timeout=timedelta(seconds=1))
    assert met is False
    assert timeouts == []


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


@pytest.mark.parametrize("length", [1, 5, 6, 8, 36])
def test_submission_from_prefix(project, detached, length):
    job, saved = history_entry(project, detached)
    loaded = Submission.from_prefix(project, job.id[:length])
    assert loaded.model_dump(mode="json") == saved


@pytest.mark.parametrize("prefix", ["", "../jobs", "abcde*", "zzzzzz"])
def test_submission_rejects_invalid_prefix(project, prefix):
    with pytest.raises(ValidationError, match="nonempty hexadecimal UUID prefix"):
        Submission.from_prefix(project, prefix)


def test_submission_missing_prefix(project):
    with pytest.raises(ExecutionError, match="No submission matches"):
        Submission.from_prefix(project, "abcdef")


def test_submission_ambiguous_prefix_and_table_ids(project, detached, monkeypatch):
    monkeypatch.chdir(project.root)
    job, _ = history_entry(project, detached, final=True)
    entry = Submission.load(project, job.id)
    (project.root / ".hiraku/jobs" / f"{job.id}.json").unlink()
    for digit in ("0", "1"):
        entry.id = f"12345678-{digit}000-4000-8000-000000000001"
        entry.save(project)
    for prefix in ("1", "12345678"):
        with pytest.raises(ValidationError, match="Ambiguous.*more characters"):
            Submission.from_prefix(project, prefix)
    assert Submission.from_prefix(project, "12345678-0").id.startswith("12345678-0")
    result = CliRunner().invoke(main, ["job", "list"], env={"COLUMNS": "320"})
    assert result.exit_code == 0, result.output
    assert "12345678-0" in result.output and "12345678-1" in result.output
    assert "4000-8000" not in result.output


@pytest.mark.parametrize("removed", ["show", "sync"])
def test_cli_job_removes_show_and_sync(removed):
    result = CliRunner().invoke(main, ["job", removed, "--help"])
    assert result.exit_code == 2
    assert "No such command" in result.output


def test_cli_job_status_includes_command(project, detached, monkeypatch):
    monkeypatch.chdir(project.root)
    job, _ = history_entry(project, detached)
    monkeypatch.setattr(Submission, "get_psi_job", lambda self: job)

    result = CliRunner().invoke(main, ["job", "status", job.id], env={"COLUMNS": "320"})

    assert result.exit_code == 0, result.output
    assert "command" in result.output
    assert f"{sys.executable} train.py 'argument with spaces'" in result.output


def test_history_uses_original_psij_id_and_native_spec(project, detached, monkeypatch):
    monkeypatch.chdir(project.root)
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
    path = project.root / ".hiraku/jobs" / f"{job.id}.json"
    before = path.read_bytes()
    monkeypatch.setattr(
        Submission, "save", lambda *a, **k: pytest.fail("List must not save")
    )
    assert [item.id for item in Submission.list(project)] == [job.id]
    assert path.read_bytes() == before


def test_observed_at_changes_only_on_sync(project, detached, monkeypatch):
    clock = [datetime(2026, 1, 1, tzinfo=UTC)]
    monkeypatch.setattr("hiraku.job.datetime", SimpleNamespace(now=lambda tz: clock[0]))
    job, _ = history_entry(project, detached)
    entry = Submission.load(project, job.id)
    assert entry.observed_at == entry.submitted_at == clock[0]
    native_time = entry.status.time
    clock[0] += timedelta(hours=1)
    entry.sync(job)
    assert entry.observed_at == clock[0]
    assert entry.status.time == native_time
    observed_at = entry.observed_at
    clock[0] += timedelta(hours=1)
    entry.save(project)
    assert Submission.load(project, job.id).observed_at == observed_at


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
    monkeypatch.setattr(
        psij.JobExecutor, "get_instance", lambda name, url, config: detached
    )
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


def test_sync_failure_preserves_snapshot(project, detached, monkeypatch):
    job, _ = history_entry(project, detached)
    entry = Submission.load(project, job.id)
    before = entry.model_dump()

    def unavailable(self):
        raise ObservationError("backend unavailable")

    monkeypatch.setattr(Submission, "get_psi_job", unavailable)
    with pytest.raises(ObservationError, match="backend unavailable"):
        entry.sync()
    assert entry.model_dump() == before
    job.status = psij.JobStatus(psij.JobState.ACTIVE, exit_code="invalid")
    with pytest.raises(ObservationError, match="Cannot read status"):
        entry.sync(job)
    assert entry.model_dump() == before
    assert Submission.load(project, job.id) == entry


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
def test_sync_error_preserves_snapshot_and_other_jobs(
    project, detached, monkeypatch, final
):
    monkeypatch.chdir(project.root)
    job, entry = history_entry(project, detached, final=final)
    other, completed = history_entry(project, detached, record_id=None, final=True)

    def unavailable(*args, **kwargs):
        raise psij.SubmitException("backend unavailable")

    monkeypatch.setattr(psij.JobExecutor, "get_instance", unavailable)
    response = CliRunner().invoke(main, ["job", "status", job.id, "--json"])
    if final:
        assert response.exit_code == 0, response.output
        assert json.loads(response.stdout) == entry
    else:
        assert response.exit_code == 1, response.output
        assert "backend unavailable" in response.stderr
    assert Submission.load(project, job.id).model_dump(mode="json") == entry
    entries = Submission.list(project)
    assert [item.id for item in entries] == [other.id, job.id]
    assert entries[0].status.model_dump() == completed["status"]
    assert len(Submission.list(project, record_id="test")) == 1
    listed = CliRunner().invoke(main, ["job", "list", "--json"])
    assert listed.exit_code == 0, listed.output
    assert [item["id"] for item in json.loads(listed.stdout)] == [other.id, job.id]


def test_record_filter_avoids_unrelated_backend_queries(project, detached, monkeypatch):
    monkeypatch.chdir(project.root)
    history_entry(project, detached)
    monkeypatch.setattr(
        psij.JobExecutor, "get_instance", lambda *a, **k: pytest.fail("Unrelated job")
    )
    assert Submission.list(project, record_id="other") == []
    result = CliRunner().invoke(main, ["job", "list", "--record", "other", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []


def test_cli_status_does_not_wait_for_completion(project, detached, monkeypatch):
    monkeypatch.chdir(project.root)
    original, saved = history_entry(project, detached)

    def attach(job, native_id):
        job.executor = detached
        job._native_id = native_id
        job.status = psij.JobStatus(psij.JobState.ACTIVE)

    monkeypatch.setattr(detached, "attach", attach)
    monkeypatch.setattr(psij.JobExecutor, "get_instance", lambda *a, **k: detached)
    monkeypatch.setattr(psij.Job, "wait", lambda *a, **k: pytest.fail("Must not wait"))
    response = CliRunner().invoke(main, ["job", "status", original.id, "--json"])
    assert response.exit_code == 0, response.output
    result = json.loads(response.output)
    assert result["status"]["state"] == "ACTIVE"
    assert "observation_error" not in result
    assert result["observed_at"] != saved["observed_at"]
    assert Submission.load(project, original.id).status.state == "ACTIVE"


@pytest.mark.parametrize("state", ["COMPLETED", "FAILED", "CANCELED"])
def test_list_preserves_final_status_without_sync(
    project, detached, monkeypatch, state
):
    monkeypatch.chdir(project.root)
    job, _ = history_entry(project, detached, final=True)
    entry = Submission.load(project, job.id)
    entry.status.state = state
    entry.save(project)
    path = project.root / ".hiraku/jobs" / f"{job.id}.json"
    before = path.read_bytes()

    def unexpected(*args, **kwargs):
        pytest.fail("Final jobs must not sync or save")

    monkeypatch.setattr(Submission, "sync", unexpected)
    monkeypatch.setattr(Submission, "save", unexpected)
    response = CliRunner().invoke(main, ["job", "list", "--json"])
    assert response.exit_code == 0, response.output
    assert json.loads(response.output) == [json.loads(before)]
    assert path.read_bytes() == before
    rejected = CliRunner().invoke(main, ["job", "list", "--sync"])
    assert rejected.exit_code == 2


def test_list_syncs_nonfinal_jobs_and_continues_on_error(
    project, detached, monkeypatch
):
    monkeypatch.chdir(project.root)
    failed, saved = history_entry(project, detached)
    succeeded, _ = history_entry(project, detached, record_id=None)
    queried = []

    def sync(self):
        queried.append(self.id)
        if self.id == failed.id:
            raise ObservationError("backend unavailable")
        self.status.state = "COMPLETED"

    monkeypatch.setattr(Submission, "sync", sync)
    response = CliRunner().invoke(main, ["job", "list", "--json"])
    assert response.exit_code == 0, response.output
    results = {entry["id"]: entry for entry in json.loads(response.stdout)}
    assert set(queried) == {failed.id, succeeded.id}
    failed_result = results[failed.id]
    assert failed_result["status"]["state"] == "UNKNOWN"
    assert failed_result["observation_error"] == "backend unavailable"
    assert failed_result["observed_at"] == saved["observed_at"]
    assert Submission.load(project, failed.id).model_dump(mode="json") == saved
    assert results[succeeded.id]["status"]["state"] == "COMPLETED"
    assert Submission.load(project, succeeded.id).status.state == "COMPLETED"
    assert failed.id in response.stderr and "backend unavailable" in response.stderr
    table = CliRunner().invoke(main, ["job", "list"], env={"COLUMNS": "320"})
    assert table.exit_code == 0, table.output
    assert "UNKNOWN" in table.stdout


@pytest.mark.parametrize(
    ("state", "color"),
    [
        ("COMPLETED", "green"),
        ("FAILED", "red"),
        ("CANCELED", "red"),
        ("ACTIVE", "white"),
        ("QUEUED", "white"),
        ("NEW", "white"),
        ("UNKNOWN", "white"),
    ],
)
def test_list_colors_only_status(project, detached, monkeypatch, state, color):
    monkeypatch.chdir(project.root)
    job, _ = history_entry(project, detached)
    entry = Submission.load(project, job.id)
    entry.status.state = "ACTIVE" if state == "UNKNOWN" else state
    entry.save(project)

    def sync(self):
        if state == "UNKNOWN":
            raise ObservationError("backend unavailable")

    monkeypatch.setattr(Submission, "sync", sync)
    tables = []
    monkeypatch.setattr(
        "hiraku.cli.Console.print", lambda self, table: tables.append(table)
    )
    response = CliRunner().invoke(main, ["job", "list"])
    assert response.exit_code == 0, response.output
    table = tables[0]
    assert table.rows[0].style is None
    assert table.columns[4]._cells[0].plain == state
    assert table.columns[4]._cells[0].style == color
    for column in (0, 1, 2, 3, 5):
        assert isinstance(table.columns[column]._cells[0], str)


def test_observation_helper_propagates_save_failure(project, detached, monkeypatch):
    job, _ = history_entry(project, detached)
    entry = Submission.load(project, job.id)
    monkeypatch.setattr(Submission, "get_psi_job", lambda self: job)

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("hiraku.job.atomic_write", fail)
    with pytest.raises(ExecutionError, match="disk full"):
        observe_submission(entry, project)


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
        {"executor": ""},
        {"executor_work_directory": 3},
        {"executor_work_directory": ""},
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
        {"observed_at": "invalid"},
        {"observed_at": "2026-11-12T12:00:00"},
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
    project, detached, monkeypatch
):
    job, _ = history_entry(project, detached, final=True)
    broken = project.root / ".hiraku/jobs" / f"{psij.Job().id}.json"
    broken.write_text("{")
    monkeypatch.setattr(
        psij.JobExecutor, "get_instance", lambda *a, **k: pytest.fail("Metadata only")
    )
    with pytest.warns(RuntimeWarning, match=broken.stem):
        assert [entry.id for entry in Submission.list(project)] == [job.id]
    with pytest.warns(RuntimeWarning, match=broken.stem):
        assert Submission.list(project, record_id="unrelated") == []
    with pytest.raises(ExecutionError, match="Cannot read history"):
        Submission.load(project, broken.stem)


def test_loading_rejects_history_id_differing_from_filename(project, detached):
    job, saved = history_entry(project, detached, final=True)
    saved["id"] = psij.Job().id
    (project.jobs_dir / f"{job.id}.json").write_text(json.dumps(saved))
    with pytest.raises(ExecutionError, match="History ID differs from filename"):
        Submission.load(project, job.id)


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
    with pytest.raises(ObservationError, match="cannot be safely reattached"):
        submission.get_psi_job()
    assert submission.status.state == "QUEUED"
    assert Submission.load(project, job.id).status == submission.status
