from __future__ import annotations

import os
import subprocess
from pathlib import Path

from hiraku.config import HirakuConfig


def _git(path: Path, *arguments: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=path,
        text=True,
        capture_output=True,
        check=check,
    )
    return result.stdout.strip()


def _repository(path: Path) -> HirakuConfig:
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "config", "user.name", "Test")
    config = HirakuConfig.init(path)
    (path / "marker.txt").write_text("committed", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "initial")
    return config


def _run(
    script: Path,
    *arguments: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(script), *arguments],
        cwd=script.parents[2],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_launch_dispatcher_reports_current_commit_and_git_state(
    tmp_path: Path,
) -> None:
    config = _repository(tmp_path)
    head = _git(tmp_path, "rev-parse", "HEAD")
    script = config.dispatch_dir / "launch.sh"
    environment = os.environ.copy()
    environment.update(
        HIRAKU_RECORD_ID="task",
        HIRAKU_RECORD_FILE="/records/task.md",
        HIRAKU_ENTRYPOINT="train",
    )
    command = [
        "--",
        "/bin/sh",
        "-c",
        'printf "%s|%s|%s|%s|%s" "$HIRAKU_RECORD_ID" "$HIRAKU_RECORD_FILE" '
        '"$HIRAKU_ENTRYPOINT" "$HIRAKU_COMMIT" "$HIRAKU_GIT_STATE"',
    ]

    local = _run(script, *command, env=environment)
    assert local.returncode == 0
    assert local.stdout == f"task|/records/task.md|train|{head}|clean"
    assert local.stderr == ""

    _git(tmp_path, "commit", "--allow-empty", "-qm", "second")
    current = _git(tmp_path, "rev-parse", "HEAD")
    expected_is_ignored = _run(
        script, "--commit", "not-a-ref", *command, env=environment
    )
    assert expected_is_ignored.returncode == 0
    assert expected_is_ignored.stdout == (
        f"task|/records/task.md|train|{current}|clean"
    )
    assert expected_is_ignored.stderr == ""

    (tmp_path / "dirty.txt").write_text("dirty", encoding="utf-8")
    dirty = _run(script, *command, env=environment)
    assert dirty.returncode == 0
    assert dirty.stdout == f"task|/records/task.md|train|{current}|dirty"
    assert dirty.stderr == ""


def test_launch_dispatcher_preserves_context_and_reports_unavailable_git(
    tmp_path: Path,
) -> None:
    config = HirakuConfig.init(tmp_path)
    inherited = os.environ.copy()
    inherited.update(
        HIRAKU_RECORD_ID="stale-record",
        HIRAKU_COMMIT="stale-commit",
        HIRAKU_GIT_STATE="clean",
    )
    result = _run(
        config.dispatch_dir / "launch.sh",
        "--",
        "/bin/sh",
        "-c",
        'printf "%s|%s|%s" "$HIRAKU_RECORD_ID" "$HIRAKU_COMMIT" "$HIRAKU_GIT_STATE"',
        env=inherited,
    )
    assert result.returncode == 0
    assert result.stdout == "stale-record||unavailable"


def test_worktree_dispatcher_isolates_and_cleans_up(tmp_path: Path) -> None:
    config = _repository(tmp_path)
    commit = _git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "marker.txt").write_text("working", encoding="utf-8")
    before = _git(tmp_path, "worktree", "list", "--porcelain")

    result = _run(
        config.dispatch_dir / "worktree.sh",
        "--commit",
        commit,
        "--",
        "/bin/sh",
        "-c",
        'printf "%s|%s|%s|%s" "$(cat marker.txt)" "$HIRAKU_COMMIT" '
        '"$HIRAKU_GIT_STATE" "$HIRAKU_ROOT"',
        env={**os.environ, "HIRAKU_ROOT": str(tmp_path)},
    )

    assert result.returncode == 0
    assert result.stdout == f"committed|{commit}|clean|{tmp_path}"
    assert "worktree.sh: created temporary worktree at " in result.stderr
    assert f" ({commit})" in result.stderr
    assert "worktree.sh: running from " in result.stderr
    assert _git(tmp_path, "worktree", "list", "--porcelain") == before

    failed = _run(
        config.dispatch_dir / "worktree.sh",
        "--commit",
        commit,
        "--",
        "/bin/sh",
        "-c",
        "exit 7",
    )
    assert failed.returncode == 7
    assert _git(tmp_path, "worktree", "list", "--porcelain") == before


def _fake_slurm(
    tmp_path: Path, *, status: int = 0, agent: bool = False
) -> dict[str, str]:
    """A PATH with a fake `sbatch` that logs its arguments, keeps a copy of the job
    script, runs it at once (like a job that starts immediately) and prints
    `4242;cluster`, as `sbatch --parsable` does on a federated cluster."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    sbatch = bin_dir / "sbatch"
    sbatch.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$@" > "$FAKE_DIR/args"\n'
        "for last; do :; done\n"
        'cp "$last" "$FAKE_DIR/job.sh"\n'
        f"[ {status} -eq 0 ] || exit {status}\n"
        'SLURM_JOB_ID=4242 bash "$last" > "$FAKE_DIR/job.out" 2> "$FAKE_DIR/job.err"\n'
        'echo "4242;cluster"\n',
        encoding="utf-8",
    )
    sbatch.chmod(0o755)
    if agent:
        fake_agent = bin_dir / "hiraku-agent"
        fake_agent.write_text(
            '#!/bin/sh\nprintf "%s " "$@" > "$FAKE_DIR/agent"\n', encoding="utf-8"
        )
        fake_agent.chmod(0o755)
    fake_dir = tmp_path / "fake"
    fake_dir.mkdir()
    return {
        **os.environ,
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "FAKE_DIR": str(fake_dir),
        "HIRAKU_RECORD_ID": "exp-1",
        "HIRAKU_ROOT": str(tmp_path / "project"),
        "HIRAKU_ENTRYPOINT": "train",
    }


def test_slurm_dispatcher_submits_the_exact_commit_and_cleans_up(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config = _repository(project)
    commit = _git(project, "rev-parse", "HEAD")
    (project / "marker.txt").write_text("working", encoding="utf-8")
    (config.dispatch_dir / "slurm.header").write_text(
        "# comment\n#SBATCH --account=abc\n#SBATCH --time=01:00:00\n",
        encoding="utf-8",
    )
    env = _fake_slurm(tmp_path, agent=True)
    before = _git(project, "worktree", "list", "--porcelain")

    result = _run(
        config.dispatch_dir / "slurm.sh",
        "--commit",
        commit,
        "--",
        "/bin/sh",
        "-c",
        'printf "%s|%s|%s|%s" "$(cat marker.txt)" "$HIRAKU_COMMIT" '
        '"$HIRAKU_GIT_STATE" "$HIRAKU_RECORD_ID"',
        env=env,
    )

    fake = Path(env["FAKE_DIR"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "4242"
    # The job ran the committed file, not the working copy, with provenance set.
    assert (fake / "job.out").read_text() == f"committed|{commit}|clean|exp-1"
    job = (fake / "job.sh").read_text()
    assert "#SBATCH --account=abc" in job and "# comment" not in job
    args = (fake / "args").read_text().splitlines()
    assert "--parsable" in args
    assert "--job-name=hiraku-exp-1" in args
    assert any(arg.startswith("--output=") for arg in args)
    assert any(arg.startswith("--chdir=") and "hiraku-worktrees" in arg for arg in args)
    assert (fake / "agent").read_text().split() == [
        "mark-running",
        "exp-1",
        "--job-id",
        "4242",
    ]
    # The job removed its worktree when it ended.
    assert _git(project, "worktree", "list", "--porcelain") == before


def test_slurm_dispatcher_cleans_up_when_sbatch_fails(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config = _repository(project)
    env = _fake_slurm(tmp_path, status=3)
    before = _git(project, "worktree", "list", "--porcelain")

    result = _run(config.dispatch_dir / "slurm.sh", "--", "true", env=env)

    assert result.returncode == 3
    assert "sbatch failed" in result.stderr
    assert _git(project, "worktree", "list", "--porcelain") == before


def test_slurm_dispatcher_needs_sbatch(tmp_path: Path) -> None:
    config = _repository(tmp_path)
    result = _run(
        config.dispatch_dir / "slurm.sh",
        "--",
        "true",
        env={**os.environ, "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 1
    assert "sbatch was not found" in result.stderr


def _git_state(script: Path, env: dict[str, str]) -> str:
    result = _run(
        script, "--", "/bin/sh", "-c", 'printf "%s" "$HIRAKU_GIT_STATE"', env=env
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_launch_dispatcher_leaves_hiraku_state_out_of_the_dirty_check(
    tmp_path: Path,
) -> None:
    config = _repository(tmp_path)
    script = config.dispatch_dir / "launch.sh"
    state = "hiraku.yml\nhiraku\nruns.csv\nruns.csv.lock"
    with_state = {**os.environ, "HIRAKU_STATE_PATHS": state}
    without = {k: v for k, v in os.environ.items() if k != "HIRAKU_STATE_PATHS"}

    # A new record, a run row and a lock file (untracked), and an edit to a file that is
    # committed (the configuration, the dispatch script's own directory).
    records = config.hiraku_dir / "records"
    records.mkdir(parents=True, exist_ok=True)
    (records / "node.md").write_text("approved", encoding="utf-8")
    (tmp_path / "runs.csv").write_text("id,record_id\n", encoding="utf-8")
    (tmp_path / "runs.csv.lock").write_text("", encoding="utf-8")
    with (tmp_path / "hiraku.yml").open("a", encoding="utf-8") as handle:
        handle.write("\n# edited\n")

    assert _git_state(script, with_state) == "clean"
    # Without the list (an older `hiraku`, a hand-run script) nothing is left out.
    assert _git_state(script, without) == "dirty"

    # Code is still code: an uncommitted change to a tracked file, or a new source
    # file, makes the launch dirty however much state is also around.
    (tmp_path / "marker.txt").write_text("changed", encoding="utf-8")
    assert _git_state(script, with_state) == "dirty"
    _git(tmp_path, "checkout", "--", "marker.txt")
    assert _git_state(script, with_state) == "clean"
    (tmp_path / "train.py").write_text("print(1)", encoding="utf-8")
    assert _git_state(script, with_state) == "dirty"


def test_launch_dispatcher_handles_state_paths_with_spaces_and_glob_characters(
    tmp_path: Path,
) -> None:
    config = _repository(tmp_path)
    script = config.dispatch_dir / "launch.sh"
    odd = tmp_path / "my state [x]"
    odd.mkdir()
    (odd / "a.txt").write_text("a", encoding="utf-8")
    env = {**os.environ, "HIRAKU_STATE_PATHS": "my state [x]"}

    assert _git_state(script, env) == "clean"
    (tmp_path / "my state x").write_text("code", encoding="utf-8")
    assert _git_state(script, env) == "dirty"


def test_hiraku_launch_tells_the_dispatcher_which_paths_are_its_own_state(
    tmp_path: Path,
) -> None:
    from hiraku.launch import launch, state_paths
    from hiraku.record import Record

    config = _repository(tmp_path)
    Record.create("baseline", config=config)
    assert state_paths(config) == ["hiraku.yml", "hiraku", "runs.csv", "runs.csv.lock"]
    (config.hiraku_dir / "records" / "more.md").write_text("x", encoding="utf-8")
    (tmp_path / "runs.csv").write_text("id,record_id\n", encoding="utf-8")

    result = launch(
        {"command": ["/bin/sh", "-c", 'printf "%s" "$HIRAKU_GIT_STATE"']},
        record_id="baseline",
        dispatch="launch.sh",
        capture_output=True,
        config=config,
    )
    assert result.stdout == "clean"
