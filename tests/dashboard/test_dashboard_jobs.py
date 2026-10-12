from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.dashboard.capability import DashboardCapability
from hiraku.dashboard.jobs import (
    MAX_COMPLETED_JOBS,
    JobLogs,
    LaunchController,
    LaunchRequest,
    parse_override_yaml,
)
from hiraku.errors import EntrypointNotFoundError, LaunchError, ValidationError
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


def test_parse_override_yaml_preserves_types() -> None:
    overrides = parse_override_yaml(
        "seed: 1\nverbose: true\nnothing: null\nitems: [a, b]\n"
    )
    assert overrides == {
        "seed": 1,
        "verbose": True,
        "nothing": None,
        "items": ["a", "b"],
    }


def test_preview_renders_merged_argv(tmp_path: Path) -> None:
    config = _project(tmp_path, [sys.executable, "evaluate.py"])
    controller = _controller(config, tmp_path)
    preview = controller.preview(
        LaunchRequest(
            job_id="preview-1",
            record_id="root",
            entrypoint="run",
            args={"seed": 1, "verbose": True},
        )
    )
    assert preview.record_id == "root"
    assert preview.entrypoint == "run"
    assert preview.argv == (
        sys.executable,
        "evaluate.py",
        "--seed",
        "1",
        "--verbose",
    )
    assert preview.dispatcher == "launch.sh"


def test_changed_entrypoint_requires_another_preview(tmp_path: Path) -> None:
    config = _project(tmp_path, [sys.executable, "evaluate.py"])
    controller = _controller(config, tmp_path)
    request = _request()
    preview = controller.preview(request)
    path = config.records_dir / "root" / "root.md"
    path.write_text(path.read_text().replace("evaluate.py", "changed.py"))
    with pytest.raises(ValidationError, match="preview again"):
        controller.submit(replace(request, reviewed=preview))
    assert controller.active_job_id is None


def test_worktree_requires_commit_before_spawning(tmp_path: Path) -> None:
    config = _project(tmp_path, [sys.executable, "evaluate.py"])
    controller = _controller(config, tmp_path)
    with pytest.raises(ValidationError, match="requires a resolvable commit"):
        controller.submit(replace(_request(), dispatch="worktree"))
    assert controller.active_job_id is None


def test_preview_resolves_record_local_commit(tmp_path, monkeypatch):
    from hiraku.dashboard.jobs import GitRepository

    config = _project(tmp_path, [sys.executable, "evaluate.py"])
    path = config.records_dir / "root" / "root.md"
    path.write_text(
        path.read_text().replace("id: root", "id: root\ncommit: " + "b" * 40)
    )
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.setattr(GitRepository, "resolve", lambda self, ref: "a" * 40)
    preview = _controller(config, tmp_path).preview(_request())
    assert preview.commit == "a" * 40
    assert preview.commit_source == "record"


def test_log_tail_is_bounded_with_multibyte_text(tmp_path: Path) -> None:
    from hiraku.dashboard.jobs import TAIL_BYTES, _tail

    path = tmp_path / "stdout.log"
    path.write_bytes(("é" * TAIL_BYTES).encode())
    assert len(_tail(path).encode()) == TAIL_BYTES


def test_shared_controller_survives_client_reconnect(tmp_path, monkeypatch):
    from hiraku.dashboard.supervisor import connect_controller, supervise

    config = _project(tmp_path, [sys.executable, "-c", "import time; time.sleep(1)"])
    environment = {}
    with supervise(config, environment) as owner:
        for key, value in environment.items():
            monkeypatch.setenv(key, value)
        first = connect_controller()
        first.submit(_request("shared"))
        second = connect_controller()
        assert second.poll()[0].job_id == "shared"
        with pytest.raises(ValidationError, match="already active"):
            second.submit(_request("other"))
        directory = owner._temp_dir
    assert not directory.exists()


def test_bad_override_types_cannot_start(tmp_path: Path) -> None:
    config = _project(tmp_path, [sys.executable, "-c", "print('hi')"])
    controller = _controller(config, tmp_path)
    with pytest.raises(ValidationError):
        controller.submit(
            LaunchRequest(
                job_id="bad-args",
                record_id="root",
                entrypoint="run",
                args={"nested": {"not": "scalar"}},
            )
        )
    assert controller.active_job_id is None


def test_missing_dispatcher_cannot_start(tmp_path: Path) -> None:
    config = _project(tmp_path, [sys.executable, "-c", "print('hi')"])
    controller = _controller(config, tmp_path)
    with pytest.raises(LaunchError):
        controller.submit(
            LaunchRequest(
                job_id="bad-dispatch",
                record_id="root",
                entrypoint="run",
                args={},
                dispatch="missing",
            )
        )
    assert controller.active_job_id is None


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
    with pytest.raises(ValidationError, match="Duplicate"):
        controller.submit(_request(job_ids[0]))
