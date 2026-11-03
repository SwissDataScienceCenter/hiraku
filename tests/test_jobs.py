"""Job persistence and tracker attribution."""

import json
import sys
from types import SimpleNamespace

import pytest

from hiraku.config import HirakuConfig
from hiraku.errors import ValidationError
from hiraku.execution import execute_command
from hiraku.jobs import ComputeBackend, Job, current_job
from hiraku.record import Record


def test_foreground_job_attribution(tmp_path):
    config = HirakuConfig.init(tmp_path)
    Record.create("demo", config=config)
    result = execute_command(
        [sys.executable, "-c", "import os; print(os.environ['HIRAKU_JOB_ID'])"],
        record_id="demo",
        config=config,
        capture_output=True,
    )
    job = Job.load(config.hiraku_dir / "jobs", result.stdout.strip())
    assert job.state == "completed"
    assert job.record_id == "demo"


def test_submit_persists_native_id(tmp_path, monkeypatch):
    import hiraku.jobs as jobs

    config = HirakuConfig.init(tmp_path)
    Record.create("demo", config=config)

    class NativeJob:
        def __init__(self, spec):
            self.spec = spec
            self.native_id = "123"

    submitted = []

    class Executor:
        def submit(self, job):
            assert job.spec.environment["HIRAKU_JOB_ID"]
            submitted.append(job.spec)

    monkeypatch.setattr(jobs, "PsiJob", NativeJob)
    monkeypatch.setattr(jobs, "JobSpec", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        jobs,
        "JobExecutor",
        SimpleNamespace(get_instance=lambda name: Executor()),
    )
    job = jobs.submit(["echo", "ok"], record_id="demo", config=config)
    assert jobs.ComputeBackend().executor_name == "local"
    stored = json.loads((config.hiraku_dir / "jobs" / f"{job.id}.json").read_text())
    assert stored["native_id"] == "123"
    assert stored["state"] == "submitted"
    assert submitted[0].executable == "echo"
    assert submitted[0].arguments == ["ok"]


def test_attribution_conflict(monkeypatch):
    monkeypatch.setenv("HIRAKU_JOB_ID", "job-1")
    from hiraku.tracking.attribution import _tracking_metadata

    assert _tracking_metadata({})["hiraku.job_id"] == "job-1"
    with pytest.raises(ValidationError):
        _tracking_metadata({"hiraku.job_id": "other"})


def test_tracking_attribution_resolves_persisted_job(tmp_path, monkeypatch):
    config = HirakuConfig.init(tmp_path)
    Record.create("demo", config=config)
    job = Job(
        record_id="demo",
        command=["python", "train.py"],
        directory=str(tmp_path),
        commit="abc123",
        git_state="dirty",
    )
    job.save(config.hiraku_dir / "jobs")
    monkeypatch.setenv("HIRAKU_JOB_ID", job.id)
    monkeypatch.setenv("HIRAKU_ROOT", str(tmp_path))

    current = current_job()

    assert current is not None
    assert current.id == job.id
    assert current.record_id == "demo"
    assert current.commit == "abc123"
    assert current.git_state == "dirty"


@pytest.mark.parametrize(
    ("module_name", "attribute", "argument"),
    [
        ("mlflow", "start_run", "tags"),
        ("wandb", "init", "config"),
    ],
)
def test_tracking_context_adds_job_attribution(
    tmp_path, monkeypatch, module_name, attribute, argument
):
    from contextlib import contextmanager

    config = HirakuConfig.init(tmp_path)
    Record.create("demo", config=config)
    job = Job(
        record_id="demo",
        command=["python", "train.py"],
        directory=str(tmp_path),
        commit="abc123",
        git_state="clean",
    )
    job.save(config.hiraku_dir / "jobs")
    monkeypatch.setenv("HIRAKU_JOB_ID", job.id)
    monkeypatch.setenv("HIRAKU_ROOT", str(tmp_path))
    captured = {}

    if module_name == "mlflow":

        @contextmanager
        def start(*args, **kwargs):
            captured.update(kwargs)
            yield object()
    else:

        def start(*args, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(finish=lambda: None)

    monkeypatch.setitem(sys.modules, module_name, SimpleNamespace(**{attribute: start}))
    from hiraku import track_run

    with track_run(backend=module_name):
        pass

    assert captured[argument] == {
        "hiraku.job_id": job.id,
        "hiraku.record_id": "demo",
        "hiraku.commit": "abc123",
        "hiraku.git_state": "clean",
    }


def test_job_selects_runs_from_tracking_backend(tmp_path):
    from hiraku.run import Run

    job = Job(record_id="demo", command=["python"], directory=str(tmp_path))
    runs = [Run(id="one", job_id=job.id), Run(id="other", job_id="other")]
    backend = SimpleNamespace(list_runs=lambda *, record_id: runs)
    assert [run.id for run in job.tracked_runs(backend)] == ["one"]


def test_cancel_delegates_to_backend(tmp_path, monkeypatch):
    from hiraku.jobs import cancel

    job = Job(
        record_id="demo",
        command=["python"],
        directory=str(tmp_path),
        executor="slurm",
        native_id="123",
        state="submitted",
    )
    job.save(tmp_path)
    requested = []

    def fake_cancel(self, folder, job_id):
        requested.append(job_id)
        return Job.load(folder, job_id)

    monkeypatch.setattr(ComputeBackend, "cancel", fake_cancel)
    config = HirakuConfig.init(tmp_path)
    job.save(config.hiraku_dir / "jobs")
    result = cancel(config, job.id)
    assert requested == [job.id]
    assert result.state == "submitted"


def test_cancel_refuses_reused_local_pid(tmp_path, monkeypatch):
    import psutil

    from hiraku.errors import ExecutionError

    job = Job(
        record_id="demo",
        command=["python"],
        directory=str(tmp_path),
        executor="local",
        native_id="123",
        native_process_created=1.0,
    )
    job.save(tmp_path)
    backend = ComputeBackend("local")
    monkeypatch.setattr(backend, "refresh", lambda folder, job_id: job)
    monkeypatch.setattr(
        psutil, "Process", lambda pid: SimpleNamespace(create_time=lambda: 2.0)
    )
    with pytest.raises(ExecutionError, match="reused"):
        backend.cancel(tmp_path, job.id)


def test_job_wait_propagates_exit_code(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from hiraku.cli import main

    HirakuConfig.init(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("hiraku.cli.follow_job", lambda directory, job_id: 7)
    result = CliRunner().invoke(main, ["job", "wait", "job-id"])
    assert result.exit_code == 7


def test_job_group_uses_singular_command():
    from click.testing import CliRunner

    from hiraku.cli import main

    runner = CliRunner()
    assert runner.invoke(main, ["job", "--help"]).exit_code == 0
    plural = runner.invoke(main, ["jobs", "--help"])
    assert plural.exit_code != 0
    assert "No such command 'jobs'" in plural.output


def test_jobs_submit_uses_local_backend(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from hiraku.cli import main

    config = HirakuConfig.init(tmp_path)
    Record.create("demo", config=config)
    monkeypatch.chdir(tmp_path)
    calls = []

    def fake_submit(command, *, record_id, config, executor="local", dispatch=None):
        calls.append((command, record_id, executor))
        return SimpleNamespace(id="job-123")

    monkeypatch.setattr("hiraku.jobs.submit", fake_submit)
    result = CliRunner().invoke(
        main, ["job", "submit", "--record", "demo", "--", "echo", "ok"]
    )
    assert result.exit_code == 0, result.output
    assert "Job submitted with ID job-123" in result.output
    assert calls == [(["echo", "ok"], "demo", "local")]


def test_jobs_run_rejects_slurm(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from hiraku.cli import main

    HirakuConfig.init(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["job", "run", "--slurm", "--", "echo"])
    assert result.exit_code != 0
    assert "No such option '--slurm'" in result.output


def test_refresh_uses_psi_status_and_exit_code(tmp_path, monkeypatch):
    from psij import JobState

    job = Job(
        record_id="demo",
        command=["false"],
        directory=str(tmp_path),
        executor="local",
        native_id="1",
    )
    job.save(tmp_path)

    class Native:
        def wait(self, **kwargs):
            return SimpleNamespace(state=JobState.FAILED, exit_code=7)

    monkeypatch.setattr(ComputeBackend, "_attach", lambda self, item: (None, Native()))

    updated = ComputeBackend().refresh(tmp_path, job.id)

    assert updated.state == "failed"
    assert updated.exit_code == 7


def test_refresh_keeps_terminal_state(tmp_path):
    from hiraku.jobs import ComputeBackend

    job = Job(
        record_id="demo",
        command=["false"],
        directory=str(tmp_path),
        executor="local",
        native_id="1",
        state="canceled",
    )
    job.save(tmp_path)
    assert ComputeBackend().refresh(tmp_path, job.id).state == "canceled"


def test_local_wait_uses_original_psi_j_status(tmp_path):
    from hiraku.jobs import submit

    config = HirakuConfig.init(tmp_path)
    Record.create("demo", config=config)
    backend = ComputeBackend()
    job = submit(
        [sys.executable, "-c", "raise SystemExit(4)"],
        record_id="demo",
        config=config,
        _backend=backend,
    )

    completed = backend.wait(
        config.hiraku_dir / "jobs", job.id, timeout=10, poll_interval=0.05
    )

    assert completed.state == "failed"
    assert completed.exit_code == 4


def test_cancel_local_job(tmp_path):
    import subprocess

    config = HirakuConfig.init(tmp_path)
    Record.create("demo", config=config)
    script = (
        "import sys; from pathlib import Path; "
        "from hiraku.config import HirakuConfig; from hiraku.jobs import submit; "
        "c = HirakuConfig.load(Path(sys.argv[1])); "
        "print(submit([sys.executable, '-c', "
        "'import time; time.sleep(30)'], "
        "record_id='demo', config=c).id)"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(config.path)],
        capture_output=True,
        text=True,
        check=True,
    )
    from hiraku.jobs import cancel

    canceled = cancel(config, result.stdout.strip())
    assert canceled.state == "canceled"
