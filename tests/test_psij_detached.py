"""The detached executor owns execution independently of Hiraku metadata."""

import gc
import json
import socket
import subprocess
import sys
import threading
import time
import weakref
from datetime import timedelta

import psij
import pytest


@pytest.fixture
def executor(tmp_path):
    return psij.JobExecutor.get_instance("detached", url=str(tmp_path))


def test_submitter_can_exit_and_fresh_process_recovers_exit_code(tmp_path):
    script = """
import sys
import psij
from pathlib import Path
root = Path(sys.argv[1])
executor = psij.JobExecutor.get_instance('detached', url=str(root))
job = psij.Job(psij.JobSpec(
    executable=sys.executable,
    arguments=['-c', "import time; time.sleep(.5); print('out'); raise SystemExit(7)"],
    stdout_path=root/'stdout.log', stderr_path=root/'stderr.log',
))
executor.submit(job)
print(job.native_id)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    executor = psij.JobExecutor.get_instance("detached", url=str(tmp_path))
    attached = psij.Job()
    executor.attach(attached, result.stdout.strip())
    status = attached.wait(timeout=timedelta(seconds=10))
    assert status and status.state == psij.JobState.FAILED
    assert status.exit_code == 7
    assert attached.native_id in executor.list()
    assert (tmp_path / "stdout.log").read_text() == "out\n"
    recovered = psij.Job()
    executor.attach(recovered, attached.native_id)
    assert recovered.status.exit_code == 7
    assert not list(tmp_path.rglob("job.json"))


def test_cancel_stops_command_and_is_idempotent(executor, tmp_path):
    gate = tmp_path / "release"
    marker = tmp_path / "unexpected"
    job = psij.Job(
        psij.JobSpec(
            executable=sys.executable,
            arguments=[
                "-c",
                "import time; from pathlib import Path\n"
                f"while not Path({str(gate)!r}).exists(): time.sleep(.05)\n"
                f"Path({str(marker)!r}).touch()",
            ],
            stdout_path=tmp_path / "stdout",
            stderr_path=tmp_path / "stderr",
        )
    )
    executor.submit(job)
    try:
        executor.cancel(job)
        assert job.status.state == psij.JobState.CANCELED
        executor.cancel(job)
    finally:
        gate.touch()
    assert not marker.exists()


def test_stale_state_does_not_invent_an_outcome(executor, tmp_path):
    native_id = "a" * 32
    directory = tmp_path / native_id
    directory.mkdir()
    data = {
        "state": "ACTIVE",
        "time": time.time() - 100,
        "exit_code": None,
        "message": None,
        "host": socket.gethostname(),
        "updated_at": time.time() - 60,
        "metadata": {},
    }
    path = directory / "status.json"
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    with pytest.raises(psij.SubmitException, match="outcome unknown"):
        executor.attach(psij.Job(), native_id)
    assert path.read_bytes() == before
    data.update(state="FAILED", exit_code=9)
    path.write_text(json.dumps(data))
    job = psij.Job()
    executor.attach(job, native_id)
    assert job.status.exit_code == 9  # Final status remains valid after worker exit.


@pytest.mark.parametrize("native_id", ["../escape", "", "wrong"])
def test_invalid_native_ids(executor, native_id):
    with pytest.raises(psij.InvalidJobException):
        executor.attach(psij.Job(), native_id)


def test_missing_state_is_an_observation_error(executor):
    with pytest.raises(psij.SubmitException, match="Cannot observe local job"):
        executor.attach(psij.Job(), "b" * 32)


def test_builtin_local_registration_is_unchanged(executor):
    assert (
        psij.JobExecutor.get_instance("local").__class__.__module__
        == "psij.executors.local"
    )


def test_discarding_attached_handle_stops_its_monitor(executor, tmp_path):
    native_id = "c" * 32
    directory = tmp_path / native_id
    directory.mkdir()
    (directory / "status.json").write_text(
        json.dumps(
            {
                "state": "ACTIVE",
                "time": time.time(),
                "exit_code": None,
                "message": None,
                "host": socket.gethostname(),
                "updated_at": time.time(),
                "metadata": {},
            }
        )
    )
    before = set(threading.enumerate())
    job = psij.Job()
    executor.attach(job, native_id)
    monitors = [thread for thread in threading.enumerate() if thread not in before]
    reference = weakref.ref(job)
    del job
    gc.collect()
    for thread in monitors:
        thread.join(timeout=2)
        assert not thread.is_alive()
    assert reference() is None
