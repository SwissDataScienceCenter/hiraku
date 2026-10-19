from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from hiraku.compute.slurm import SlurmBackend
from hiraku.job import JobSpec


def test_submitit_backend_submits_spec_and_reads_lifecycle(
    tmp_path: Path, monkeypatch
) -> None:
    import hiraku.compute.slurm as slurm

    job_dir = tmp_path / "jobs" / "hiraku-job"
    submitit_folder = job_dir / "submitit"
    log_paths = SimpleNamespace(
        stdout=submitit_folder / "stdout.log",
        stderr=submitit_folder / "stderr.log",
    )
    log_paths.stdout.parent.mkdir(parents=True)
    log_paths.stdout.write_text("out\n")
    log_paths.stderr.write_text("err\n")
    submitted = {}

    class FakeJob:
        state = "COMPLETED"
        paths = log_paths

        def __init__(self, **_kwargs):
            self.cancelled = False

        def result(self):
            return 3

        def cancel(self, *, check):
            self.cancelled = not check

    class FakeExecutor:
        def __init__(self, *, folder, cluster):
            assert cluster == "slurm"
            self.folder = Path(folder)
            self._executor = SimpleNamespace(job_class=FakeJob)

        def update_parameters(self, **parameters):
            submitted["parameters"] = parameters

        def submit(self, function, *args):
            submitted["call"] = (function, args)
            return SimpleNamespace(job_id="scheduler-id")

    monkeypatch.setattr(slurm.submitit, "AutoExecutor", FakeExecutor)
    snapshot = job_dir / "workspace"
    receipt = SimpleNamespace(
        source_path=snapshot,
        working_directory=snapshot,
        root=snapshot,
        spec=SimpleNamespace(source_dirty=False, source_digest="digest", env={}),
        id="hiraku-job",
        directory=job_dir,
        record_id="experiment-1",
        record_file=job_dir / "record.md",
        tracking_root=tmp_path,
        commit="revision",
    )
    monkeypatch.setattr(slurm, "_read", lambda _path: receipt)
    backend = SlurmBackend(tmp_path / "jobs", timeout_min=30)
    spec = JobSpec(
        job_id="hiraku-job",
        command=("python", "train.py", "--seed", "7"),
        record_id="experiment-1",
        record_snapshot={},
        project_path="",
        revision="revision",
        source_digest="digest",
        source_dirty=False,
        dispatcher=None,
    )

    handle = backend.submit(spec)

    function, args = submitted["call"]
    assert function is slurm._run_command
    assert args[0] == ["python", "train.py", "--seed", "7"]
    assert args[1] == str(snapshot)
    assert args[2]["HIRAKU_JOB_ID"] == "hiraku-job"
    assert handle == {"folder": str(submitit_folder), "job_id": "scheduler-id"}
    assert submitted["parameters"] == {"timeout_min": 30}
    assert backend.status(handle) == {"status": "failed", "exit_code": 3}
    assert "".join(backend.logs(handle, stream="stdout", follow=False)) == "out\n"
    backend.cancel(handle)


def test_submitit_command_runs_in_selected_directory_and_returns_exit_code(
    tmp_path: Path,
) -> None:
    from hiraku.compute.slurm import _run_command

    script = tmp_path / "command.py"
    script.write_text("import os, sys; print(os.getcwd()); sys.exit(4)")
    assert _run_command(["python", str(script)], str(tmp_path), {}) == 4
