"""`launches.py`: which nodes have a launch in flight, and the CLI that records it.

Slurm is faked with a `squeue` script on PATH that reads one file per job id (its
content is the job state; no file means Slurm has forgotten the job).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.config import HirakuConfig
from hiraku.record import Record
from hiraku_agent.cli import main
from hiraku_agent.errors import AgentError
from hiraku_agent.launches import (
    MAX_RUN_AGE,
    active_launches,
    launch_dir,
    mark_done,
    mark_running,
    normalize_job_id,
    run_in_flight,
    run_status,
    running_nodes,
)
from hiraku_agent.node import create_node
from hiraku_agent.schedule import suggest_next
from hiraku_agent.state import node_state

COMMIT = "a" * 40


@pytest.fixture
def config(tmp_path: Path) -> HirakuConfig:
    return HirakuConfig.init(tmp_path, install_skill=False)


@pytest.fixture
def slurm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake `squeue`. `slurm / "<id>"` holding a state makes that job exist."""
    jobs = tmp_path / "fake-slurm"
    jobs.mkdir()
    binary = tmp_path / "fake-bin"
    binary.mkdir()
    script = binary / "squeue"
    script.write_text(
        "#!/bin/sh\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  case "$1" in -j) job=$2; shift 2;; *) shift;; esac\n'
        "done\n"
        f'if [ -f "{jobs}/$job" ]; then cat "{jobs}/$job"; exit 0; fi\n'
        'echo "slurm_load_jobs error: Invalid job id specified" >&2\n'
        "exit 1\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binary}{os.pathsep}{os.environ['PATH']}")
    return jobs


def _node(config: HirakuConfig, node_id: str = "baseline"):
    task = Record.create("bench", type="task", config=config)
    node = create_node(node_id, task=task, config=config)
    node.commit = COMMIT
    node.save()
    return task, node


def test_a_node_is_running_while_its_slurm_job_is(
    config: HirakuConfig, slurm: Path
) -> None:
    (slurm / "101").write_text("RUNNING\n")
    mark_running("baseline", jobs=["101"], config=config)
    assert "baseline" in active_launches(config)

    (slurm / "101").write_text("PENDING\n")
    assert "baseline" in active_launches(config)


def test_a_finished_or_forgotten_job_stops_counting(
    config: HirakuConfig, slurm: Path
) -> None:
    (slurm / "101").write_text("RUNNING\n")
    mark_running("baseline", jobs=["101"], config=config)

    (slurm / "101").write_text("COMPLETED\n")
    assert active_launches(config) == {}
    (slurm / "101").unlink()  # Slurm has forgotten it: "Invalid job id"
    assert active_launches(config) == {}


def test_one_live_job_among_several_keeps_the_node_running(
    config: HirakuConfig, slurm: Path
) -> None:
    (slurm / "1").write_text("RUNNING\n")
    (slurm / "2").write_text("RUNNING\n")
    mark_running("baseline", jobs=["1"], config=config)
    marker = mark_running("baseline", jobs=["2"], config=config)
    assert marker["jobs"] == ["1", "2"]

    (slurm / "1").write_text("COMPLETED\n")  # one chunk finished, one still going
    assert "baseline" in active_launches(config)
    (slurm / "2").write_text("FAILED\n")
    assert active_launches(config) == {}


def test_a_new_attempt_does_not_inherit_a_dead_attempts_jobs(
    config: HirakuConfig, slurm: Path
) -> None:
    (slurm / "1").write_text("COMPLETED\n")
    mark_running("baseline", jobs=["1"], config=config)
    (slurm / "2").write_text("RUNNING\n")
    marker = mark_running("baseline", jobs=["2"], config=config)
    assert marker["jobs"] == ["2"]


def test_an_unreachable_squeue_counts_as_running_not_finished(
    config: HirakuConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))  # no squeue at all
    mark_running("baseline", jobs=["7"], config=config)
    assert "baseline" in active_launches(config)


def test_mark_done_removes_the_marker(config: HirakuConfig) -> None:
    mark_running("baseline", pid=os.getpid(), config=config)
    assert mark_done("baseline", config=config) is True
    assert active_launches(config) == {}
    assert mark_done("baseline", config=config) is False


def test_a_dead_local_process_is_not_running(config: HirakuConfig) -> None:
    mark_running("baseline", pid=2**22 + 12345, config=config)
    assert active_launches(config) == {}


def test_marking_needs_a_job_or_a_pid_and_a_real_job_id(config: HirakuConfig) -> None:
    with pytest.raises(AgentError, match="Give --job-id"):
        mark_running("baseline", config=config)
    with pytest.raises(AgentError, match="not a Slurm job id"):
        mark_running("baseline", jobs=["abc"], config=config)
    assert normalize_job_id("123;cluster") == "123"
    assert normalize_job_id("123_4") == "123_4"


def test_the_marker_lives_in_the_git_dir_not_the_working_tree(
    config: HirakuConfig, tmp_path: Path
) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    mark_running("baseline", pid=os.getpid(), config=config)
    assert launch_dir(config).is_relative_to((tmp_path / ".git").resolve())
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "hiraku-launches" not in status


def test_running_beats_runs_and_an_old_verdict(config: HirakuConfig) -> None:
    _, node = _node(config)
    assert (
        node_state(node, runs=[object()], has_children=False, running=True) == "running"
    )
    node.verdict = "refutes"
    assert (
        node_state(node, runs=[object()], has_children=False, running=True) == "running"
    )
    bare = create_node(
        "other", task=Record.create("t2", type="task", config=config), config=config
    )
    assert node_state(bare, runs=[], has_children=False, running=True) == "proposed"


def test_next_waits_instead_of_resubmitting_a_running_node(
    config: HirakuConfig, slurm: Path
) -> None:
    task, node = _node(config)
    from hiraku_agent.gates import approve

    approve(node, by="q")
    assert suggest_next(task, config=config)["action"] == "launch"

    (slurm / "55").write_text("RUNNING\n")
    mark_running("baseline", jobs=["55"], config=config)
    suggestion = suggest_next(task, config=config)
    assert suggestion["action"] == "wait"
    assert "55" in suggestion["why"]

    (slurm / "55").write_text("COMPLETED\n")
    assert suggest_next(task, config=config)["action"] == "launch"


def test_cli_mark_running_running_and_mark_done(
    config: HirakuConfig, slurm: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _node(config)
    (slurm / "9").write_text("RUNNING\n")
    monkeypatch.chdir(config.root)
    runner = CliRunner()

    result = runner.invoke(main, ["mark-running", "baseline", "--job-id", "9"])
    assert result.exit_code == 0, result.output
    assert "baseline: running (Slurm job 9)" in result.output

    assert runner.invoke(main, ["running", "baseline"]).exit_code == 0
    listing = runner.invoke(main, ["running"])
    assert "baseline" in listing.output and "9" in listing.output
    as_json = json.loads(runner.invoke(main, ["running", "--json"]).output)
    assert as_json["baseline"]["jobs"] == ["9"]

    assert runner.invoke(main, ["mark-done", "baseline"]).exit_code == 0
    assert runner.invoke(main, ["running", "baseline"]).exit_code == 1
    assert "no launches in flight" in runner.invoke(main, ["running"]).output


def test_cli_mark_running_rejects_an_unknown_node(
    config: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(config.root)
    result = CliRunner().invoke(main, ["mark-running", "nope", "--job-id", "1"])
    assert result.exit_code != 0


# ------------------------------------------------ a run that reports its own status


class _Run:
    """What the tracking backends hand over: a `raw` mapping and a start time."""

    def __init__(self, raw: dict, timestamp=None, run_id: str = "r1") -> None:
        self.raw = raw
        self.timestamp = timestamp
        self.id = run_id


def test_run_status_reads_mlflow_wandb_and_csv_spellings() -> None:
    assert run_status(_Run({"status": "RUNNING"})) == "running"  # MLflow
    assert run_status(_Run({"state": "running"})) == "running"  # W&B
    assert run_status(_Run({"status": "running"})) == "running"  # CSV column
    assert run_status(_Run({})) is None
    assert run_status(_Run({"status": ""})) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"status": "RUNNING"}, True),
        ({"status": "SCHEDULED"}, True),
        ({"state": "pending"}, True),
        ({"status": "FINISHED"}, False),
        ({"status": "FAILED"}, False),
        ({"state": "crashed"}, False),
        ({}, False),  # a CSV source with no status column: not claimed to be running
    ],
)
def test_only_unfinished_statuses_count_as_in_flight(raw: dict, expected: bool) -> None:
    assert run_in_flight(_Run(raw)) is expected


def test_a_run_stuck_on_running_for_too_long_is_not_believed() -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 10, 10, tzinfo=UTC)
    fresh = _Run({"status": "RUNNING"}, timestamp=now - timedelta(hours=5))
    stale = _Run(
        {"status": "RUNNING"}, timestamp=now - MAX_RUN_AGE - timedelta(hours=1)
    )
    assert run_in_flight(fresh, now=now) is True
    assert run_in_flight(stale, now=now) is False


def test_a_node_whose_run_reports_running_is_running_without_any_marker(
    config: HirakuConfig,
) -> None:
    runs = {
        "done": [_Run({"status": "FINISHED"})],
        "going": [
            _Run({"status": "FINISHED"}, run_id="a"),
            _Run({"status": "RUNNING"}, run_id="b"),
        ],
    }
    found = running_nodes(runs, config)
    assert set(found) == {"going"}
    assert found["going"]["source"] == "tracking"
    assert found["going"]["run"] == "b"


def test_next_waits_on_a_running_run_and_describes_it(config: HirakuConfig) -> None:
    from datetime import UTC, datetime

    from hiraku_agent import schedule

    task, node = _node(config)
    node.verdict = "refutes"
    node.triaged = True
    node.save()
    run = _Run(
        {"status": "RUNNING"}, timestamp=datetime.now(UTC), run_id="mlflow-run-7"
    )
    original = type(node).runs
    type(node).runs = lambda self, **_: [run]  # type: ignore[method-assign]
    try:
        suggestion = schedule.suggest_next(task, config=config)
    finally:
        type(node).runs = original  # type: ignore[method-assign]
    assert suggestion["action"] == "wait"
    assert "mlflow-run-7 reports running" in suggestion["why"]


# ------------------------------------------------------------- a generic --check


def test_a_check_command_decides_whether_the_work_is_alive(
    config: HirakuConfig, tmp_path: Path
) -> None:
    alive = tmp_path / "alive"
    alive.write_text("x")
    mark_running("baseline", check=["test", "-f", str(alive)], config=config)
    assert "baseline" in active_launches(config)
    alive.unlink()
    assert active_launches(config) == {}


def test_cli_check_option_is_split_like_a_shell_command(
    config: HirakuConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _node(config)
    monkeypatch.chdir(config.root)
    flag = tmp_path / "flag file"
    flag.write_text("x")
    result = CliRunner().invoke(
        main, ["mark-running", "baseline", "--check", f"test -f '{flag}'"]
    )
    assert result.exit_code == 0, result.output
    assert "check test -f" in result.output
    assert CliRunner().invoke(main, ["running", "baseline"]).exit_code == 0
    flag.unlink()
    assert CliRunner().invoke(main, ["running", "baseline"]).exit_code == 1
