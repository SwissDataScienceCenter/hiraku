from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.dashboard.capability import DashboardCapability
from hiraku.dashboard.jobs import (
    MAX_COMPLETED_JOBS,
    JobLogs,
    LaunchController,
    LaunchRequest,
)
from hiraku.errors import EntrypointNotFoundError, ValidationError
from hiraku.record import Record


def _project(tmp_path: Path, entrypoint_command: list[str]) -> HirakuConfig:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    Record.create(
        "root",
        entrypoints={"run": {"command": entrypoint_command, "args": {"seed": 0}}},
        activate=False,
        config=config,
    )
    return config


def _controller(config: HirakuConfig, tmp_path: Path) -> LaunchController:
    return LaunchController(
        config,
        DashboardCapability(interactive=True),
        temp_dir=tmp_path / "jobs",
    )


def _request(job_id: str = "job-1") -> LaunchRequest:
    return LaunchRequest(
        job_id=job_id,
        record_id="root",
        entrypoint="run",
        args={"seed": 1},
    )


def _wait_terminal(
    controller: LaunchController, job_id: str, timeout: float = 10.0
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        views = controller.poll()
        job = next((view for view in views if view.job_id == job_id), None)
        if job and job.state in {"succeeded", "failed"}:
            return
        time.sleep(0.05)
    raise AssertionError(f"Job {job_id} did not finish in {timeout}s")


def test_successful_job_records_output(tmp_path: Path) -> None:
    config = _project(tmp_path, [sys.executable, "-c", "print('hello from job')"])
    controller = _controller(config, tmp_path)
    view = controller.submit(_request())
    assert view.state == "running"
    _wait_terminal(controller, view.job_id)

    views = controller.poll()
    job = next(item for item in views if item.job_id == view.job_id)
    assert job.state == "succeeded"
    assert job.exit_code == 0
    assert job.error is None
    logs = controller.logs(view.job_id)
    assert isinstance(logs, JobLogs)
    assert "hello from job" in logs.stdout_tail
    assert logs.stderr_tail == ""


def test_nonzero_exit_is_distinct_from_success(tmp_path: Path) -> None:
    command = [
        sys.executable,
        "-c",
        "import sys; print('bad output', file=sys.stderr); sys.exit(3)",
    ]
    config = _project(tmp_path, command)
    controller = _controller(config, tmp_path)
    view = controller.submit(_request())
    _wait_terminal(controller, view.job_id)

    job = next(item for item in controller.poll() if item.job_id == view.job_id)
    assert job.state == "failed"
    assert job.exit_code == 3
    assert "bad output" in job.error


def test_worker_reports_startup_failure_without_controller(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    request_path = tmp_path / "bad-request.json"
    request_path.write_text(
        json.dumps(
            {
                "project_root": str(config.root),
                "record_id": "missing",
                "entrypoint": "run",
                "args": {},
                "dispatch": None,
                "commit": None,
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "hiraku.dashboard.worker",
            str(request_path),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 2
    assert "launch failed" in result.stderr


def test_capability_required_for_launch(tmp_path: Path) -> None:
    config = _project(tmp_path, [sys.executable, "-c", "print('hi')"])
    controller = LaunchController(
        config,
        DashboardCapability(),
        temp_dir=tmp_path / "jobs",
    )
    with pytest.raises(ValidationError, match="interactive mode"):
        controller.submit(_request())


def test_duplicate_event_id_rejected(tmp_path: Path) -> None:
    config = _project(tmp_path, [sys.executable, "-c", "print('hi')"])
    controller = _controller(config, tmp_path)
    controller.submit(_request("dup"))
    with pytest.raises(ValidationError, match="Duplicate"):
        controller.submit(_request("dup"))


def test_concurrent_submission_rejected(tmp_path: Path) -> None:
    config = _project(tmp_path, [sys.executable, "-c", "import time; time.sleep(2)"])
    controller = _controller(config, tmp_path)
    controller.submit(_request("one"))
    with pytest.raises(ValidationError, match="already active"):
        controller.submit(_request("two"))
    _wait_terminal(controller, "one")


def test_poll_does_not_block_while_worker_runs(tmp_path: Path) -> None:
    config = _project(tmp_path, [sys.executable, "-c", "import time; time.sleep(2)"])
    controller = _controller(config, tmp_path)
    view = controller.submit(_request("slow"))
    started = time.monotonic()
    views = controller.poll()
    elapsed = time.monotonic() - started
    job = next(item for item in views if item.job_id == view.job_id)
    assert job.state == "running"
    assert elapsed < 1.0
    _wait_terminal(controller, view.job_id)


def test_missing_entrypoint_fails_preflight(tmp_path: Path) -> None:
    config = _project(tmp_path, [sys.executable, "-c", "print('hi')"])
    controller = _controller(config, tmp_path)
    with pytest.raises(EntrypointNotFoundError):
        controller.submit(
            LaunchRequest(
                job_id="missing-entrypoint",
                record_id="root",
                entrypoint="missing",
                args={},
            )
        )


def test_shutdown_drains_and_cleans_temp_dir(tmp_path: Path) -> None:
    config = _project(tmp_path, [sys.executable, "-c", "import time; time.sleep(30)"])
    controller = LaunchController(
        config,
        DashboardCapability(interactive=True),
    )
    temp_dir = controller._temp_dir
    view = controller.submit(_request("sleepy"))
    job = controller._jobs[view.job_id]
    controller.shutdown(timeout=0.5)
    assert job.process.poll() is not None
    assert not temp_dir.exists()


def test_completed_jobs_are_trimmed(tmp_path: Path) -> None:
    config = _project(tmp_path, [sys.executable, "-c", "print('hi')"])
    controller = _controller(config, tmp_path)
    job_ids = []
    for index in range(MAX_COMPLETED_JOBS + 1):
        job_id = f"job-{index}"
        controller.submit(_request(job_id))
        _wait_terminal(controller, job_id)
        job_ids.append(job_id)
    views = controller.poll()
    kept = {view.job_id for view in views}
    assert len(kept) == MAX_COMPLETED_JOBS
    assert job_ids[0] not in kept
    assert job_ids[-1] in kept
