"""Local job execution and tracker attribution."""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from hiraku.errors import ValidationError
from hiraku.jobs import Job, JobSpec, JobState, LocalJobExecutor


def test_local_job_executes_in_foreground_with_environment(tmp_path):
    spec = JobSpec(
        executable=sys.executable,
        arguments=(
            "-c",
            "import json, os; print(json.dumps([os.getcwd(), "
            "os.environ['HIRAKU_JOB_ID'], "
            "os.environ['HIRAKU_RECORD_ID']]))",
        ),
        directory=tmp_path,
        capture_output=True,
    )
    job = Job(spec, record_id="demo")

    result = job.execute(LocalJobExecutor())

    assert result.returncode == 0
    assert json.loads(result.stdout) == [str(tmp_path), job.id, "demo"]
    assert job.status.state == JobState.COMPLETED
    assert job.status.exit_code == 0


def test_local_job_records_nonzero_exit_status(tmp_path):
    job = Job(
        JobSpec(
            executable=sys.executable,
            arguments=("-c", "raise SystemExit(7)"),
            directory=tmp_path,
            capture_output=True,
        )
    )

    result = job.execute()

    assert result.returncode == 7
    assert job.status.state == JobState.FAILED
    assert job.status.exit_code == 7


def test_local_job_marks_ctrl_c_as_canceled(monkeypatch, tmp_path):
    import hiraku.jobs as jobs

    job = Job(JobSpec(executable="python", directory=tmp_path))

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(jobs.subprocess, "run", interrupt)
    with pytest.raises(KeyboardInterrupt):
        job.execute()

    assert job.status.state == JobState.CANCELED


def test_job_spec_validates_argv_and_environment(tmp_path):
    with pytest.raises(ValidationError, match="arguments"):
        JobSpec(executable="python", arguments=("bad\x00arg",), directory=tmp_path)
    with pytest.raises(ValidationError, match="environment"):
        JobSpec(
            executable="python",
            directory=tmp_path,
            environment={"HIRAKU_RECORD_ID": 7},  # type: ignore[dict-item]
        )


def test_job_selects_tracked_runs(tmp_path):
    from hiraku.run import Run

    job = Job(
        JobSpec(executable="python", directory=tmp_path),
        record_id="demo",
    )
    runs = [Run(id="one", job_id=job.id), Run(id="other", job_id="other")]
    backend = SimpleNamespace(list_runs=lambda *, record_id: runs)

    assert [run.id for run in job.tracked_runs(backend)] == ["one"]


def test_attribution_conflict(monkeypatch):
    monkeypatch.setenv("HIRAKU_JOB_ID", "job-1")
    from hiraku.tracking.attribution import _tracking_metadata

    assert _tracking_metadata({})["hiraku.job_id"] == "job-1"
    with pytest.raises(ValidationError):
        _tracking_metadata({"hiraku.job_id": "other"})


@pytest.mark.parametrize(
    ("module_name", "attribute", "argument"),
    [("mlflow", "start_run", "tags"), ("wandb", "init", "config")],
)
def test_tracking_context_adds_environment_attribution(
    monkeypatch, module_name, attribute, argument
):
    monkeypatch.setenv("HIRAKU_JOB_ID", "job-123")
    monkeypatch.setenv("HIRAKU_RECORD_ID", "demo")
    monkeypatch.setenv("HIRAKU_COMMIT", "abc123")
    monkeypatch.setenv("HIRAKU_GIT_STATE", "clean")
    captured = {}

    if module_name == "mlflow":

        @contextmanager
        def start(*args, **kwargs):
            captured.update(kwargs)
            yield object()
    else:

        class WandbRun:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                captured["exit_exception"] = exc_type

        def start(*args, **kwargs):
            captured.update(kwargs)
            return WandbRun()

    monkeypatch.setitem(sys.modules, module_name, SimpleNamespace(**{attribute: start}))
    from hiraku import track_run

    if module_name == "wandb":
        with (
            pytest.raises(RuntimeError, match="training failed"),
            track_run(backend=module_name),
        ):
            raise RuntimeError("training failed")
    else:
        with track_run(backend=module_name):
            pass

    assert captured[argument] == {
        "hiraku.job_id": "job-123",
        "hiraku.record_id": "demo",
        "hiraku.commit": "abc123",
        "hiraku.git_state": "clean",
    }
    if module_name == "wandb":
        assert captured["exit_exception"] is RuntimeError
