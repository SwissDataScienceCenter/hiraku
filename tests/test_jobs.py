from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku import (
    HirakuConfig,
    Job,
    Record,
    cancel_job,
    job_logs,
    job_status,
    list_jobs,
    load_job,
    submit_job,
    wait_job,
)
from hiraku.cli import main
from hiraku.errors import JobError, ValidationError

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX local jobs")


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def project(tmp_path: Path) -> HirakuConfig:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    config = HirakuConfig.init(tmp_path, install_skill=False)
    Record.create(
        "root", command=[sys.executable, "-c", "print('default')"], config=config
    )
    (tmp_path / "marker.txt").write_text("original")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "initial")
    return config


def submit(config: HirakuConfig, code: str, **kwargs) -> Job:
    job = submit_job(
        [sys.executable, "-c", code],
        record_id="root",
        config=config,
        **kwargs,
    )
    assert isinstance(job, Job)
    return job


def test_default_command_and_literal_argv_in_source_snapshot(project):
    job = submit(
        project,
        "import json,os,sys; from pathlib import Path; "
        "Path('output.txt').write_text('retained'); "
        "print(json.dumps([os.getcwd(),os.environ['HIRAKU_JOB_ID'],"
        "os.environ['HIRAKU_RECORD_ID'],os.environ['HIRAKU_COMMIT'],"
        "os.environ['HIRAKU_ROOT'],os.environ['HIRAKU_TRACKING_ROOT'],"
        "os.environ['HIRAKU_RECORD_FILE'],"
        "Path('marker.txt').read_text()]))",
    )
    done = wait_job(job.id, timeout=10, config=project)
    assert done.status == "succeeded" and done.exit_code == 0
    (
        cwd,
        identity,
        record,
        commit,
        job_root,
        tracking_root,
        record_file,
        marker,
    ) = json.loads("".join(job_logs(job.id, config=project)))
    assert Path(cwd) == job.working_directory
    assert cwd != str(project.root)
    assert (identity, record, commit) == (
        job.id,
        "root",
        git(project.root, "rev-parse", "HEAD"),
    )
    assert Path(job_root) == job.working_directory
    assert Path(tracking_root) == project.root
    assert Path(record_file) == job.record_file and Path(record_file).is_file()
    assert marker == "original"
    assert not hasattr(job.spec, "working_directory")
    assert isinstance(job.spec.command, tuple)
    assert (job.working_directory / "output.txt").read_text() == "retained"
    assert not (project.root / "output.txt").exists()
    assert job.record_snapshot["id"] == "root"
    assert job.spec.source_digest
    assert job.spec.source_dirty is False
    assert git(project.root, "status", "--porcelain") == ""
    default = submit_job(record_id="root", config=project)
    assert wait_job(default.id, timeout=10, config=project).status == "succeeded"
    assert "".join(job_logs(default.id, config=project)) == "default\n"


def test_bare_run_shows_help_without_submitting(project, monkeypatch):
    monkeypatch.chdir(project.root)
    result = CliRunner().invoke(main, ["run"])
    assert result.exit_code == 0
    assert "Run a project command as a durable local job" in result.output
    assert not project.jobs_dir.exists()

    result = CliRunner().invoke(main, ["run", "--record", "root"])
    assert result.exit_code == 0
    assert len(list_jobs(config=project)) == 1


def test_run_rejects_target_alias(project, monkeypatch):
    monkeypatch.chdir(project.root)
    result = CliRunner().invoke(
        main,
        ["run", "--record", "root", "--target", "local"],
    )
    assert result.exit_code == 2
    assert "No such option '--target'" in result.output
    assert not project.jobs_dir.exists()

    result = CliRunner().invoke(main, ["run", "--record", "root", "--backend", "local"])
    assert result.exit_code == 2
    assert "No such option '--backend'" in result.output
    assert not project.jobs_dir.exists()


def test_cli_detaches_and_preserves_literal_arguments(project):
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "hiraku.cli",
            "run",
            "--record",
            "root",
            "--",
            sys.executable,
            "-c",
            "import sys,time,json; time.sleep(0.5); print(json.dumps(sys.argv[1:]))",
            "--record",
            "literal value",
            "$(echo untouched)",
            "--x=a=b",
            "",
        ],
        cwd=project.root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    identity = result.stdout.strip()
    assert wait_job(identity, timeout=10, config=project).status == "succeeded"
    inspect = subprocess.run(
        [sys.executable, "-m", "hiraku.cli", "job", "status", identity, "--json"],
        cwd=project.root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert json.loads(inspect.stdout)["status"] == "succeeded"
    assert json.loads("".join(job_logs(identity, config=project))) == [
        "--record",
        "literal value",
        "$(echo untouched)",
        "--x=a=b",
        "",
    ]


def test_explicit_command_does_not_require_a_default_and_run_json(project, monkeypatch):
    record = Record.load(project.records_dir / "root/root.md")
    record.command = None
    record.save(overwrite=True)
    project.command = None
    git(project.root, "add", ".")
    git(project.root, "commit", "-qm", "remove default command")
    monkeypatch.chdir(project.root)

    result = CliRunner().invoke(
        main,
        [
            "run",
            "--record",
            "root",
            "--json",
            "--",
            sys.executable,
            "-c",
            "print('explicit')",
            "$(literal)",
        ],
    )
    assert result.exit_code == 0, result.output
    receipt = json.loads(result.output)
    assert receipt["id"]
    assert receipt["spec"]["command"] == [
        sys.executable,
        "-c",
        "print('explicit')",
        "$(literal)",
    ]
    done = wait_job(receipt["id"], timeout=10, config=project)
    assert done.status == "succeeded"
    assert "explicit" in "".join(job_logs(receipt["id"], config=project))


def test_missing_explicit_and_default_command_has_actionable_error(project):
    record = Record.load(project.records_dir / "root/root.md")
    record.command = None
    record.save(overwrite=True)
    project.command = None
    with pytest.raises(ValidationError, match="supply a command after --"):
        submit_job(record_id="root", config=project)


@pytest.mark.parametrize("kind", ["unstaged", "staged", "untracked"])
def test_dirty_sources_are_rejected(project, kind):
    path = project.root / ("new.txt" if kind == "untracked" else "marker.txt")
    path.write_text("changed")
    if kind == "staged":
        git(project.root, "add", ".")
    with pytest.raises(JobError, match="Git working tree"):
        submit(project, "print('captured')")
    assert not project.jobs_dir.exists()


def test_source_snapshot_digest_is_stable_and_ignored_files_are_excluded(project):
    from hiraku.git import GitRepository
    from hiraku.source import prepare_source

    ignored = project.root / "private-data.bin"
    ignored.write_bytes(b"do not capture")
    git(project.root, "config", "core.excludesfile", str(project.root / ".ignore"))
    (project.root / ".ignore").write_text("private-data.bin\n")
    git(project.root, "add", ".ignore")
    git(project.root, "commit", "-qm", "add ignored data rule")
    first = project.root.with_name(project.root.name + "-snapshot-one")
    second = project.root.with_name(project.root.name + "-snapshot-two")
    snapshot_one = prepare_source(GitRepository(project), first)
    snapshot_two = prepare_source(GitRepository(project), second)
    assert snapshot_one.digest == snapshot_two.digest
    assert snapshot_one.dirty is False and snapshot_two.dirty is False
    assert (first / "marker.txt").read_text() == "original"
    assert not (first / "private-data.bin").exists()


def test_pinned_source_uses_the_requested_revision(project):
    from hiraku.git import GitRepository
    from hiraku.source import prepare_source

    (project.root / "metadata.txt").write_text("must not be export-filtered")
    (project.root / ".gitattributes").write_text("metadata.txt export-ignore\n")
    git(project.root, "add", ".")
    git(project.root, "commit", "-qm", "source with export attribute")
    first = git(project.root, "rev-parse", "HEAD")
    (project.root / "marker.txt").write_text("later checkout")
    git(project.root, "add", "marker.txt")
    git(project.root, "commit", "-qm", "later source")
    snapshot = prepare_source(
        GitRepository(project), project.root.parent / "pinned", first
    )
    assert snapshot.base_revision == first
    assert snapshot.dirty is False
    assert (snapshot.path / "marker.txt").read_text() == "original"
    assert (snapshot.path / "metadata.txt").read_text() == "must not be export-filtered"


def test_source_snapshot_rejects_external_symlinks(project):
    from hiraku.errors import JobError
    from hiraku.git import GitRepository
    from hiraku.source import prepare_source

    (project.root / "outside-link").symlink_to("/etc/passwd")
    git(project.root, "add", "outside-link")
    git(project.root, "commit", "-qm", "add unsafe source symlink")
    with pytest.raises(JobError, match="symlink escapes"):
        prepare_source(GitRepository(project), project.root / "unsafe-snapshot")


def test_revision_selection_and_selected_dispatcher(project):
    first = git(project.root, "rev-parse", "HEAD")
    (project.root / "marker.txt").write_text("new")
    dispatcher = project.dispatch_dir / "launch.sh"
    dispatcher.write_text('#!/bin/sh\necho selected-dispatcher\nshift\nexec "$@"\n')
    git(project.root, "add", ".")
    git(project.root, "commit", "-qm", "second")
    record = Record.load(project.records_dir / "root/root.md")
    record.commit = first
    record.save(overwrite=True)
    git(project.root, "add", ".")
    git(project.root, "commit", "-qm", "bind record")
    job = submit(
        project, "from pathlib import Path; print(Path('marker.txt').read_text())"
    )
    assert wait_job(job.id, timeout=10, config=project).commit == first
    assert "".join(job_logs(job.id, config=project)) == "original\n"
    explicit = submit(project, "print('explicit')", commit="HEAD")
    assert wait_job(explicit.id, timeout=10, config=project).status == "succeeded"
    assert (
        "".join(job_logs(explicit.id, config=project))
        == "selected-dispatcher\nexplicit\n"
    )


def test_failed_job_logs_and_cli_exit_code(project, monkeypatch):
    job = submit(
        project, "import sys; print('out'); print('err',file=sys.stderr); sys.exit(7)"
    )
    assert wait_job(job.id, timeout=10, config=project).status == "failed"
    assert "".join(job_logs(job.id, stream="stderr", config=project)) == "err\n"
    monkeypatch.chdir(project.root)
    assert CliRunner().invoke(main, ["job", "wait", job.id]).exit_code == 7
    broken = submit_job(["no-such-program-hiraku"], record_id="root", config=project)
    assert wait_job(broken.id, timeout=10, config=project).status == "failed"


def test_cancel_process_group_and_idempotence(project):
    job = submit(
        project,
        "import subprocess,sys,time; from pathlib import Path; "
        "p=subprocess.Popen([sys.executable,'-c',"
        '"import time; from pathlib import Path; time.sleep(3); '
        "Path('escaped').touch()\"]); "
        "Path('ready').touch(); time.sleep(60)",
    )
    deadline = time.monotonic() + 10
    while not (job.working_directory / "ready").exists():
        assert time.monotonic() < deadline
        time.sleep(0.02)
    with ThreadPoolExecutor() as pool:
        list(pool.map(lambda _: cancel_job(job.id, config=project), range(5)))
    assert wait_job(job.id, timeout=10, config=project).status == "cancelled"
    assert cancel_job(job.id, config=project).status == "cancelled"
    time.sleep(3)
    assert not (job.working_directory / "escaped").exists()


@pytest.mark.skipif(
    not Path("/proc").exists(), reason="Linux process identity required"
)
def test_cancel_reaches_child_group_after_supervisor_dies(project):
    job = submit(
        project,
        "import subprocess,sys,time; from pathlib import Path; "
        "subprocess.Popen([sys.executable,'-c',"
        '"import time; from pathlib import Path; time.sleep(3); '
        "Path('escaped-after-supervisor-death').touch()\"]); "
        "Path('ready').touch(); time.sleep(60)",
    )
    deadline = time.monotonic() + 10
    while not (job.working_directory / "ready").exists():
        assert time.monotonic() < deadline
        time.sleep(0.02)
    supervisor_pid = job.backend_handle["supervisor_pid"]
    os.kill(supervisor_pid, signal.SIGKILL)
    os.waitpid(supervisor_pid, 0)
    time.sleep(0.1)
    cancel_job(job.id, config=project)
    time.sleep(3)
    assert not (job.working_directory / "escaped-after-supervisor-death").exists()


def test_concurrent_submissions_and_terminal_cancel_race(project):
    with ThreadPoolExecutor() as pool:
        jobs = list(pool.map(lambda _: submit(project, "print('done')"), range(3)))
    assert len({job.id for job in jobs}) == 3
    for job in jobs:
        assert wait_job(job.id, timeout=10, config=project).status == "succeeded"
        assert cancel_job(job.id, config=project).status == "succeeded"
    assert len(list_jobs(record_id="root", config=project)) == 3
    assert list_jobs(record_id="absent", config=project) == []


def test_unavailable_supervisor_is_unknown_without_signalling_pid(project, monkeypatch):
    job = submit(project, "print('done')")
    wait_job(job.id, timeout=10, config=project)
    # Simulate a stale nonterminal manifest and a reused PID without killing a worker.
    manifest = job.directory / "job.json"
    data = json.loads(manifest.read_text())
    data.update(
        status="running",
        backend_handle={"directory": str(job.directory), "supervisor_pid": os.getpid()},
    )
    manifest.write_text(json.dumps(data))
    state_path = job.directory / "local-state.json"
    state = json.loads(state_path.read_text())
    state["status"] = "running"
    state["supervisor_pid"] = os.getpid()
    state_path.write_text(json.dumps(state))
    os.utime(job.directory / "heartbeat", (0, 0))
    calls = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: calls.append((pid, sig)))
    assert job_status(job.id, config=project).status == "lost"
    assert calls == []
    assert load_job(job.id, config=project).status == "lost"


def test_dry_run_and_invalid_inputs(project):
    preview = submit_job(record_id="root", dry_run=True, config=project)
    assert preview["command"][-1] == "print('default')"
    assert not project.jobs_dir.exists()
    with pytest.raises(ValidationError, match="reserved"):
        submit(project, "pass", env={"HIRAKU_JOB_ID": "fake"})
    with pytest.raises(ValidationError, match="reserved"):
        submit(project, "pass", env={"HIRAKU_TRACKING_ROOT": "/elsewhere"})
    with pytest.raises(ValidationError, match="Unsupported compute backend"):
        submit_job(record_id="root", backend="hpc", config=project)
    with pytest.raises(ValidationError, match="Invalid job ID"):
        load_job("../bad", config=project)
    with pytest.raises(ValidationError, match="dispatcher"):
        submit(project, "pass", dispatch="../bad")
    with pytest.raises(ValidationError, match="argv sequence"):
        submit_job("echo bad", record_id="root", config=project)


def test_dispatcher_failure_and_uncommitted_dispatcher(project):
    from hiraku.errors import GitError

    with pytest.raises(JobError, match="does not exist"):
        submit(project, "pass", dispatch="missing")
    with pytest.raises(GitError):
        submit(project, "pass", commit="no-such-revision")
    script = project.dispatch_dir / "fail.sh"
    script.write_text("#!/bin/sh\necho setup-failed >&2\nexit 9\n")
    script.chmod(0o755)
    git(project.root, "add", ".")
    git(project.root, "commit", "-qm", "failing dispatcher")
    job = submit(project, "pass", dispatch="fail")
    done = wait_job(job.id, timeout=10, config=project)
    assert done.status == "failed" and done.exit_code == 9
    assert "setup-failed" in "".join(job_logs(job.id, stream="stderr", config=project))


def test_environment_passed_without_persisting_values(project):
    secret = "test-value-not-for-manifest"
    job = submit(
        project,
        "import os; print(os.environ['TEST_JOB_VARIABLE'])",
        env={"TEST_JOB_VARIABLE": secret},
    )
    assert wait_job(job.id, timeout=10, config=project).status == "succeeded"
    assert "".join(job_logs(job.id, config=project)).strip() == secret
    assert job.spec.env["TEST_JOB_VARIABLE"] == secret


def test_submission_spawn_failure_retains_failed_manifest(project, monkeypatch):
    import hiraku.compute.local as local

    original = local.subprocess.Popen

    def fail_supervisor(arguments, *args, **kwargs):
        if "_supervise" in str(arguments):
            raise OSError("could not start supervisor")
        return original(arguments, *args, **kwargs)

    monkeypatch.setattr(local.subprocess, "Popen", fail_supervisor)
    with pytest.raises(JobError, match="could not start supervisor"):
        submit(project, "pass")
    jobs = list_jobs(config=project)
    assert len(jobs) == 1 and jobs[0].status == "failed"
    assert "could not start supervisor" in jobs[0].error


def test_ambiguous_supervisor_start_retains_reconcilable_job(project, monkeypatch):
    import hiraku.compute.local as local

    class StartedProcess:
        pid = 999_999

        @staticmethod
        def poll():
            return None

    original_popen = local.subprocess.Popen

    def delayed_supervisor(arguments, *args, **kwargs):
        if "_supervise" in str(arguments):
            return StartedProcess()
        return original_popen(arguments, *args, **kwargs)

    monkeypatch.setattr(local.subprocess, "Popen", delayed_supervisor)
    monkeypatch.setattr(local, "_read_state", lambda _directory: {"status": "unknown"})
    ticks = iter(range(1000))
    monkeypatch.setattr(local.time, "monotonic", lambda: next(ticks) * 0.1)
    monkeypatch.setattr(local.time, "sleep", lambda _delay: None)
    with pytest.raises(JobError, match="startup timed out"):
        submit(project, "pass")
    jobs = list_jobs(config=project)
    assert len(jobs) == 1 and jobs[0].status == "unknown"
    assert jobs[0].backend_handle == {"directory": str(jobs[0].directory)}


def test_slow_source_preparation_is_not_listed_before_receipt(project, monkeypatch):
    import hiraku.job as jobs_module

    original = jobs_module.prepare_source
    preparing = threading.Event()

    def slow_prepare(repository, destination, revision=None):
        preparing.set()
        time.sleep(0.4)
        return original(repository, destination, revision)

    monkeypatch.setattr(jobs_module, "prepare_source", slow_prepare)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(submit, project, "print('prepared')")
        assert preparing.wait(5)
        assert list_jobs(config=project) == []
        job = future.result(timeout=10)
    assert wait_job(job.id, timeout=10, config=project).status == "succeeded"


def test_follow_logs_and_timeout(project):
    job = submit(
        project,
        "import time; print('first',flush=True); time.sleep(0.5); print('last')",
    )
    with pytest.raises(JobError, match="Timed out"):
        wait_job(job.id, timeout=0, config=project)
    assert "".join(job_logs(job.id, follow=True, config=project)) == "first\nlast\n"


def test_wait_retries_unknown_observations_and_reports_monitoring_timeout(
    project, monkeypatch
):
    import hiraku.job as jobs_module

    job = submit(project, "print('done')")
    done = wait_job(job.id, timeout=10, config=project)
    unknown = done.model_copy(update={"status": "unknown"})
    results = iter([unknown, done])
    monkeypatch.setattr(
        jobs_module,
        "job_status",
        lambda *_args, **_kwargs: next(results),
    )
    monkeypatch.setattr(jobs_module.time, "sleep", lambda _delay: None)
    assert wait_job(job.id, timeout=1, config=project).status == "succeeded"

    monkeypatch.setattr(jobs_module, "job_status", lambda *_args, **_kwargs: unknown)
    with pytest.raises(JobError, match="Could not observe"):
        wait_job(job.id, unknown_timeout=0, config=project)


@pytest.mark.parametrize("value", [[], "python train.py", [""], [3], ["x\x00"]])
def test_records_reject_invalid_commands(value):
    with pytest.raises((ValidationError, ValueError)):
        Record(id="root", command=value)


def test_project_config_has_no_custom_command_validator(tmp_path):
    config = HirakuConfig(path=tmp_path / "hiraku.yml", root=tmp_path, command=[])
    assert config.command == []


def test_command_inheritance_roundtrip_and_project_fallback(tmp_path):
    config = HirakuConfig.init(tmp_path, install_skill=False)
    config.command = ["project-command"]
    config.save()
    root = Record.create("root", command=["root-command"], config=config)
    child = Record.create("child", parent_id=root.id, config=config)
    assert child.resolve_command(config=config) == ["root-command"]
    child.command = ["replacement", "--flag"]
    child.save(overwrite=True)
    assert Record.load(child.path).resolve_command(config=config) == [
        "replacement",
        "--flag",
    ]
    child.command = None
    child.save(overwrite=True)
    assert child.resolve_command(config=config) == ["project-command"]
    assert HirakuConfig.load(config.path).command == ["project-command"]
    with pytest.raises(ValidationError, match="no longer supported"):
        Record(id="old", entrypoints={})
    old_config = HirakuConfig(path=config.path, root=tmp_path, entrypoints={})
    assert old_config.model_extra["entrypoints"] == {}
