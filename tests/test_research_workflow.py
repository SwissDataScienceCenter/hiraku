"""Scripted end-to-end tests for the bundled research workflow."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from hiraku.config import HirakuConfig
from hiraku.context import Context
from hiraku.record import Record, load_record
from hiraku.workflows.session import Session, load_session
from hiraku.workflows.workflow import load_workflow

HEADER = "id,record_id,commit,entrypoint,metric.val\n"


class Harness:
    """Scripted stand-in for a coding agent driving one session."""

    def __init__(self, config: HirakuConfig) -> None:
        self.config = config
        self.workflow = load_workflow("research", config=config)
        self.response: dict[str, Any] = {}

    def start(self, **arguments: Any) -> dict[str, Any]:
        result = Session.run_start(self.workflow, arguments)
        self.response = self._deliver(result)
        return self.response

    def send(self, event: str, **arguments: Any) -> dict[str, Any]:
        session = load_session(self.response["session_id"], config=self.config)
        result = session.run_event(
            self.workflow, self.response["instruction_id"], event, arguments
        )
        response = self._deliver(result)
        if response["status"] == "accepted":
            self.response = response
        return response

    def _deliver(self, result) -> dict[str, Any]:
        response = result.model_dump(exclude_none=True)
        if result.status == "accepted":
            response.update(
                load_session(result.session_id, config=self.config).get_instruction(
                    self.workflow
                )
            )
        return response

    @property
    def node(self) -> str:
        return self.instruction["text"].splitlines()[0]

    @property
    def instruction(self) -> dict[str, Any]:
        return self.response["instruction"]

    @property
    def data(self) -> dict[str, Any]:
        return self.instruction["data"]


def git(config: HirakuConfig, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=config.root, check=True, capture_output=True, text=True
    ).stdout.strip()


def commit(config: HirakuConfig, message: str) -> str:
    (config.root / "train.py").write_text(f"# {message}\n")
    git(config, "add", "train.py")
    git(config, "commit", "-q", "-m", message)
    return git(config, "rev-parse", "HEAD")


def add_run(
    config: HirakuConfig, run_id: str, record_id: str, sha: str, value: str = ""
) -> None:
    path = config.root / "runs.csv"
    if not path.exists():
        path.write_text(HEADER)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"{run_id},{record_id},{sha},train,{value}\n")


def plan(record_id: str) -> dict[str, str]:
    return {
        "record_id": record_id,
        "summary": f"Try {record_id}",
        "hypothesis": f"{record_id} helps",
        "expected": "val increases by 0.01",
        "change": f"Apply {record_id}",
    }


def add_context(config: HirakuConfig, context_id: str, text: str = "Notes") -> None:
    Context(id=context_id, markdown=text).save(
        config.contexts_dir / context_id / "context.md"
    )


def make_project(
    tmp_path: Path, *, task: bool = True, setup: bool = True
) -> HirakuConfig:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    if setup:
        add_context(config, "project-setup", "Runs are recorded in runs.csv.")
    git(config, "init", "-q", "-b", "main")
    git(config, "config", "user.email", "test@example.org")
    git(config, "config", "user.name", "Test")
    commit(config, "base")
    if task:
        make_task(config, "task")
    return config


def make_task(config: HirakuConfig, record_id: str, **fields: Any) -> None:
    values = {
        "entrypoints": {"train": {"command": ["python", "train.py"]}},
        "metrics": [{"name": "val", "direction": "maximize"}],
        **fields,
    }
    Record.create(record_id, type="task", config=config, **values)


@pytest.fixture
def config(tmp_path: Path) -> HirakuConfig:
    return make_project(tmp_path)


def run_through(harness: Harness, config: HirakuConfig, value: str = "0.9") -> str:
    """Implement, launch, and finish the run of the assigned experiment."""
    assert harness.node == "## Implement"
    record_id = harness.data["experiment"]["record_id"]
    sha = commit(config, record_id)
    assert harness.send("implemented", commit="HEAD")["status"] == "accepted"
    assert harness.node == "## Launch"
    assert record_id in harness.data["command"]
    assert harness.send("launched")["status"] == "accepted"
    add_run(config, f"run-{record_id}", record_id, sha, value)
    return record_id


def test_graph_loads_and_draws(config: HirakuConfig) -> None:
    workflow = load_workflow("research", config=config)
    workflow.graph.check_reachability()
    chart = workflow.graph.to_mermaid()
    for name in ("design", "implement", "launch", "wait", "analyze", "triage"):
        assert name in chart


def test_full_study_builds_a_tree(config: HirakuConfig) -> None:
    harness = Harness(config)
    harness.start(max_experiments=4, max_depth=2)
    assert harness.node == "## Design"
    assert harness.data["parent"]["record_id"] == "task"
    assert harness.data["remaining"] == 4
    assert "approval" in harness.instruction["text"]

    harness.send("designed", experiments=[plan("lr"), plan("dropout")])
    # Both are implemented and launched before any result is in.
    first = run_through(harness, config)
    assert first == "lr"
    assert harness.node == "## Implement"
    assert harness.data["experiment"]["record_id"] == "dropout"
    sha = commit(config, "dropout")
    harness.send("implemented", commit="HEAD")
    harness.send("launched")
    # dropout's run has no metrics yet: only lr can be analyzed.
    add_run(config, "run-dropout", "dropout", sha)
    assert harness.node == "## Analyze"
    assert harness.data["experiment"]["record_id"] == "lr"
    assert harness.data["metrics"] == [{"name": "val", "direction": "maximize"}]
    assert harness.data["runs"][0]["metrics"] == {"val": "0.9"}

    harness.send("analyzed", verdict="supports", conclusion="run-lr: val 0.9")
    assert harness.node == "## Triage"
    assert harness.data["can_follow_up"] is True
    harness.send("triaged", decision="follow-up", reason="Tune the schedule")
    assert harness.node == "## Design"
    assert harness.data["parent"]["record_id"] == "lr"
    assert harness.data["depth"] == 2
    harness.send("designed", experiments=[plan("lr-cosine")])
    assert load_record("lr-cosine", config=config).parent_id == "lr"

    # The follow-up is implemented while dropout still waits for metrics.
    run_through(harness, config, "0.95")
    # Nothing else is open, so the workflow waits until a run has metrics.
    assert harness.node == "## Wait"
    assert harness.send("checked")["status"] == "accepted"
    assert harness.node == "## Analyze"
    assert harness.data["experiment"]["record_id"] == "lr-cosine"
    assert harness.data["parent"]["record_id"] == "lr"
    harness.send("analyzed", verdict="supports", conclusion="run-lr-cosine: 0.95")
    assert harness.data["can_follow_up"] is False  # at max depth
    rejected = harness.send("triaged", decision="follow-up", reason="More")
    assert rejected["status"] == "rejected"
    harness.send("triaged", decision="stop", reason="Good enough")

    # Only dropout is left, and it is still running.
    assert harness.node == "## Wait"
    assert harness.data["launched"][0]["record_id"] == "dropout"
    assert harness.send("checked")["status"] == "not_ready"
    add_run(config, "run-dropout-2", "dropout", sha, "0.7")
    assert harness.send("checked")["status"] == "accepted"
    assert harness.node == "## Analyze"
    harness.send("analyzed", verdict="refutes", conclusion="run-dropout-2: 0.7")
    harness.send("triaged", decision="stop", reason="Worse")

    assert harness.node == "## Done"
    tree = {item["id"]: item for item in harness.data["tree"]}
    assert set(tree) == {"lr", "dropout", "lr-cosine"}
    assert tree["lr-cosine"]["depth"] == 2
    assert all(item["step"] == "done" for item in tree.values())
    session = load_session(harness.response["session_id"], config=config)
    assert session.status == "completed"


def test_drafts_a_task_when_none_is_given(tmp_path: Path) -> None:
    config = make_project(tmp_path, task=False)
    harness = Harness(config)
    harness.start()
    assert harness.node == "## Draft"
    Record.create(
        "loose",
        type="task",
        config=config,
        entrypoints={"train": {"command": ["python", "train.py"]}},
    )
    rejected = harness.send("drafted_task", record_id="loose")
    assert rejected["status"] == "rejected"
    assert "metrics" in rejected["message"]
    make_task(config, "goal")
    harness.send("drafted_task", record_id="goal")
    assert harness.node == "## Design"
    assert harness.data["task"] == "goal"


def test_design_respects_limits_and_ids(config: HirakuConfig) -> None:
    harness = Harness(config)
    harness.start(max_experiments=2)
    too_many = harness.send("designed", experiments=[plan("a"), plan("b"), plan("c")])
    assert too_many["status"] == "rejected"
    taken = harness.send("designed", experiments=[plan("task")])
    assert taken["status"] == "rejected"
    twice = harness.send("designed", experiments=[plan("a"), plan("a")])
    assert twice["status"] == "rejected"
    assert harness.send("designed", experiments=[plan("a")])["status"] == "accepted"


def test_failed_runs_are_repaired_then_given_up(config: HirakuConfig) -> None:
    harness = Harness(config)
    harness.start(max_repairs=1, max_depth=1)
    harness.send("designed", experiments=[plan("broken")])
    run = commit(config, "broken")
    harness.send("implemented", commit=run)
    harness.send("failed", record_id="broken", reason="ModuleNotFoundError: torch")
    assert harness.node == "## Implement"
    assert harness.data["failure"] == "ModuleNotFoundError: torch"
    commit(config, "broken again")
    harness.send("implemented", commit="HEAD")
    harness.send("launched")
    assert harness.node == "## Wait"
    harness.send("failed", record_id="broken", reason="CUDA out of memory")
    assert harness.node == "## Triage"
    record = load_record("broken", config=config)
    assert record.verdict == "inconclusive"
    assert "CUDA out of memory" in record.conclusion
    assert harness.data["can_follow_up"] is False  # max_depth=1


def test_runs_at_another_commit_are_ignored(config: HirakuConfig) -> None:
    harness = Harness(config)
    harness.start(max_depth=1)
    harness.send("designed", experiments=[plan("exp")])
    harness.send("implemented", commit=commit(config, "exp"))
    harness.send("launched")
    add_run(config, "stale", "exp", git(config, "rev-parse", "HEAD~1"), "0.5")
    assert harness.node == "## Wait"
    assert harness.send("checked")["status"] == "not_ready"


def test_finish_ends_early_and_resume_picks_up_the_tree(config: HirakuConfig) -> None:
    harness = Harness(config)
    harness.start()
    harness.send("designed", experiments=[plan("one"), plan("two")])
    run_through(harness, config)
    harness.send("finish")
    assert harness.node == "## Done"

    # A new session on the same task continues from the evidence on the records.
    again = Harness(config)
    again.start(task="task")
    # Implementing "two" starts compute, so it comes before analyzing "one".
    assert again.node == "## Implement"
    assert again.data["experiment"]["record_id"] == "two"
    again.send("finish")


def test_autonomous_setting_drops_the_approval_request(config: HirakuConfig) -> None:
    path = config.workflows_dir / "research" / "workflow.md"
    text = path.read_text()
    path.write_text(
        text.replace("id: research\n", "id: research\nautonomous: true\n", 1)
    )
    harness = Harness(config)
    harness.start()
    assert "Decide on your own" in harness.instruction["text"]


def test_launches_come_before_analysis(config: HirakuConfig) -> None:
    harness = Harness(config)
    harness.start(max_depth=1)
    harness.send("designed", experiments=[plan("first"), plan("second")])
    first = run_through(harness, config)  # first has a run with metrics now
    assert first == "first"
    # Implementing "second" starts compute, so it comes before analyzing "first".
    assert harness.node == "## Implement"
    assert harness.data["experiment"]["record_id"] == "second"
    harness.send("implemented", commit=commit(config, "second"))
    assert harness.node == "## Launch"
    harness.send("launched")
    assert harness.node == "## Analyze"
    harness.send("analyzed", verdict="supports", conclusion="run-first: 0.9")
    # Triage follows its own analysis before other work.
    assert harness.node == "## Triage"
    assert harness.data["experiment"]["record_id"] == "first"


def test_overview_describes_the_tree(config: HirakuConfig) -> None:
    harness = Harness(config)
    harness.start(max_depth=2)
    harness.send("designed", experiments=[plan("a"), plan("b")])
    run_through(harness, config)
    harness.send("finish")

    overview = load_workflow("research", config=config).overview("task")
    assert overview["task"]["record_id"] == "task"
    assert overview["task"]["metrics"] == [{"name": "val", "direction": "maximize"}]
    items = {item["record_id"]: item for item in overview["experiments"]}
    assert items["a"]["step"] == "analyze"
    assert items["a"]["runs"][0]["metrics"] == {"val": "0.9"}
    assert items["b"]["step"] == "implement"
    assert items["b"]["parent"] == "task"
    assert items["b"]["hypothesis"] == "b helps"


def fake_sbatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sbatch = bin_dir / "sbatch"
    sbatch.write_text("#!/bin/sh\necho 42\n")
    sbatch.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def test_setup_comes_first_until_the_project_can_run_experiments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_project(tmp_path / "project", setup=False)
    # Start without a Slurm dispatcher, whether or not one is bundled.
    for name in ("slurm.sh", "slurm.header"):
        (config.dispatch_dir / name).unlink(missing_ok=True)
    fake_sbatch(tmp_path, monkeypatch)
    harness = Harness(config)
    harness.start()
    assert harness.node == "## Setup"
    problems = " ".join(harness.data["problems"])
    assert "project-setup" in problems and "slurm.sh" in problems
    assert harness.data["slurm"] is True
    assert "contexts" not in (harness.instruction.get("context") or {})

    rejected = harness.send("set_up")
    assert rejected["status"] == "rejected"

    add_context(config, "project-setup")
    text = config.path.read_text().replace(
        "default_dispatch: launch.sh", "default_dispatch: slurm.sh"
    )
    config.path.write_text(text)
    missing = harness.send("set_up")
    assert missing["status"] == "rejected"
    assert "slurm.sh is not in hiraku/dispatch" in missing["message"]

    dispatcher = config.dispatch_dir / "slurm.sh"
    dispatcher.write_text("#!/bin/sh\n")
    dispatcher.chmod(0o755)
    header = config.dispatch_dir / "slurm.header"
    header.write_text("# Site settings\n")
    still = harness.send("set_up")
    assert still["status"] == "rejected"
    assert "slurm.header" in still["message"]

    header.write_text("#SBATCH --partition=normal\n")
    assert harness.send("set_up")["status"] == "accepted"
    assert harness.node == "## Design"


def test_instructions_refer_to_setup_and_record_contexts(
    config: HirakuConfig,
) -> None:
    add_context(config, "protocol")
    add_context(config, "papers")
    task = load_record("task", config=config)
    task.contexts = ["protocol", "missing"]
    task.save()
    harness = Harness(config)
    harness.start(max_depth=2)
    references = harness.instruction["context"]
    assert references["contexts"] == ["project-setup", "protocol"]
    assert "Read the Hiraku contexts" in harness.instruction["text"]

    harness.send("designed", experiments=[plan("exp")])
    experiment = load_record("exp", config=config)
    experiment.contexts = ["papers"]
    experiment.save()
    harness.send("implemented", commit=commit(config, "exp"))
    assert harness.node == "## Launch"
    references = harness.instruction["context"]
    assert references["contexts"] == ["project-setup", "protocol", "papers"]
