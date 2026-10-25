from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hiraku.config import HirakuConfig
from hiraku.git import GitRepository
from hiraku.record import Record, load_record
from hiraku.workflows.session import Session, load_session
from hiraku.workflows.workflow import list_workflows, load_workflow

HEADER = "id,record_id,commit,metric.val\n"


class Harness:
    """Scripted stand-in for a coding harness driving one session."""

    def __init__(self, config: HirakuConfig) -> None:
        self.config = config
        self.workflows = {
            workflow.id: workflow for workflow in list_workflows(config=config)
        }
        self.response: dict[str, Any] = {}

    def start(self, workflow: str, **arguments: Any) -> dict[str, Any]:
        self.workflow = self.workflows[workflow]
        session = Session.create(self.workflow)
        with session.lock():
            result = session.run_start(self.workflow, arguments)
        self.response = self.deliver(session, result)
        return self.response

    def send(self, event: str, **arguments: Any) -> dict[str, Any]:
        session = load_session(self.response["session_id"], config=self.workflow.config)
        with session.lock():
            result = session.run_event(
                self.workflow, self.response["instruction_id"], event, arguments
            )
        result = self.deliver(session, result)
        if result["status"] == "accepted":
            self.response = result
        return result

    def deliver(self, session, result):
        response = {"session_id": session.id, **result.model_dump(exclude_none=True)}
        if result.status == "accepted":
            response.update(session.get_instruction(self.workflow))
        return response

    @property
    def instruction(self) -> dict[str, Any]:
        return self.response["instruction"]

    @property
    def events(self) -> list[str]:
        return [item["event"] for item in self.instruction["events"]["accepted"]]

    def section(self) -> str:
        return self.instruction["text"].splitlines()[0]


def experiment(config: HirakuConfig, record_id: str) -> None:
    Record.create(
        record_id,
        parent_id="task",
        type="experiment",
        activate=False,
        config=config,
        summary=f"Try {record_id}",
        hypothesis="It helps",
    )


def add_run(
    config: HirakuConfig,
    run_id: str,
    record_id: str,
    value: str,
    commit: str,
) -> None:
    path = config.root / "runs.csv"
    if not path.exists():
        path.write_text(HEADER)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"{run_id},{record_id},{commit},{value}\n")


def successful_run(config: HirakuConfig, record_id: str):
    return SimpleNamespace(
        id=f"r-{record_id}", commit=GitRepository(config).resolve("HEAD")
    )


@pytest.fixture
def config(tmp_path: Path) -> HirakuConfig:
    selected = HirakuConfig.init(tmp_path, install_skill=False)
    Record.create(
        "task",
        type="task",
        config=selected,
    )
    experiment(selected, "base")
    return selected


def commit(config: HirakuConfig, message: str) -> str:
    def git(*arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments],
            cwd=config.root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    if not (config.root / ".git").exists():
        git("init", "-b", "main")
        git("config", "user.email", "test@example.org")
        git("config", "user.name", "Test")
    (config.root / ".gitignore").write_text(
        "hiraku/\nhiraku.yml\n.mcp.json\n.codex/\n.agents/\n.claude/\n"
    )
    (config.root / "train.py").write_text(f"# {message}\n")
    git("add", "-A")
    git("add", "-f", "hiraku/dispatch/launch.sh")
    git("commit", "-m", message)
    return git("rev-parse", "HEAD")


def draft(harness: Harness, record_id: str, summary: str = "Add dropout") -> dict:
    return harness.send(
        "drafted",
        record_id=record_id,
        summary=summary,
        hypothesis="Dropout reduces overfitting",
        change="Add dropout 0.3 before the head",
    )


def run_experiment(harness: Harness, config: HirakuConfig, record_id: str) -> str:
    """Take a drafted experiment through implement, launch, and analyze."""
    sha = commit(config, record_id)
    harness.send("implemented", commit="HEAD")
    tracked_run = successful_run(config, record_id)
    add_run(config, f"r-{record_id}", record_id, "0.8", tracked_run.commit)
    harness.send("launched", run_id=tracked_run.id)
    harness.send("analyzed", verdict="supports", conclusion="Better")
    return sha


def start_collaborative(harness: Harness, **arguments):
    arguments.setdefault("command", ["python", "train.py"])
    return harness.start("collaborative", task="task", **arguments)


def test_collaborative_runs_each_step_and_stops_after_the_rounds(
    config: HirakuConfig,
) -> None:
    harness = Harness(config)
    started = start_collaborative(harness, rounds=2)
    assert started["status"] == "accepted"
    assert harness.section() == "## Draft"
    # Until gates exist, the agent gets the user's approval in the conversation.
    text = " ".join(harness.instruction["text"].split())
    assert "Once they approve, send the event `drafted`" in text
    assert harness.instruction["text"].endswith(
        "Never delete records or rewrite Git history."
    )
    assert harness.instruction["data"] == {"task": "task", "previous": []}
    assert harness.events == ["drafted", "revise_draft", "finish"]

    # revise redoes the current step with the user's feedback.
    harness.send("revise_draft", feedback="Try weight decay instead")
    assert harness.section() == "## Draft"
    assert harness.instruction["data"]["feedback"] == "Try weight decay instead"
    accepted = harness.instruction["events"]["accepted"]
    assert (
        accepted[1]["description"] == "Redraft the experiment with the user's feedback."
    )

    draft(harness, "c1")
    assert harness.section() == "## Implement"
    assert harness.instruction["data"]["experiment"]["summary"] == "Add dropout"
    assert "feedback" not in harness.instruction["data"]
    assert harness.events == ["implemented", "revise_implementation", "finish"]
    harness.send("revise_implementation", feedback="Keep the learning rate")
    assert harness.section() == "## Implement"
    assert harness.instruction["data"]["feedback"] == "Keep the learning rate"
    accepted = harness.instruction["events"]["accepted"]
    assert accepted[1]["description"] == (
        "Rework the implementation with the user's feedback."
    )
    assert load_record("c1", config=config).status == "drafted"

    sha = commit(config, "dropout")
    harness.send("implemented", commit="HEAD")
    assert load_record("c1", config=config).commit == sha
    assert harness.section() == "## Launch"
    assert shlex.split(harness.instruction["data"]["command"]) == [
        "hiraku",
        "run",
        "--record",
        "c1",
        "--",
        "python",
        "train.py",
    ]
    assert harness.events == ["launched", "revise_launch", "finish"]
    harness.send("revise_launch", feedback="Use the local dispatcher")
    assert harness.instruction["data"]["feedback"] == "Use the local dispatcher"

    tracked_run = successful_run(config, "c1")
    assert harness.send("launched", run_id="r1")["status"] == "not_ready"
    add_run(config, "r1", "c1", "0.8", tracked_run.commit)
    harness.send("launched", run_id="r1")
    assert harness.section() == "## Analyze"
    assert harness.instruction["data"]["runs"] == [
        {"run_id": "r1", "metrics": {"val": "0.8"}}
    ]
    assert harness.events == ["analyzed", "revise_analysis", "finish"]
    harness.send("revise_analysis", feedback="Compare against the baseline")
    assert harness.instruction["data"]["feedback"] == "Compare against the baseline"

    harness.send("analyzed", verdict="supports", conclusion="Better")
    record = load_record("c1", config=config)
    assert (record.status, record.verdict) == ("analyzed", "supports")
    assert harness.section() == "## Draft"
    assert harness.instruction["data"]["previous"] == [
        {"record_id": "c1", "verdict": "supports"}
    ]

    draft(harness, "c2")
    run_experiment(harness, config, "c2")
    assert harness.response["session_status"] == "completed"
    assert harness.section() == "## Done"
    results = harness.instruction["data"]["results"]
    assert [item["record_id"] for item in results] == ["c1", "c2"]


def test_collaborative_can_finish_from_any_step(config: HirakuConfig) -> None:
    harness = Harness(config)
    start_collaborative(harness)
    draft(harness, "c1")
    done = harness.send("finish")
    assert done["session_status"] == "completed"
    assert done["instruction"]["data"] == {"task": "task", "results": []}


def test_collaborative_rejects_invalid_input(config: HirakuConfig) -> None:
    harness = Harness(config)
    unknown = harness.start("collaborative", task="task", command=[])
    assert unknown["status"] == "rejected"
    assert "non-empty string" in unknown["message"]

    missing = harness.start("collaborative", task="task")
    assert missing["status"] == "rejected"
    start_collaborative(harness)
    exists = draft(harness, "base")
    assert exists["status"] == "rejected"
    assert "already exists" in exists["message"]
    draft(harness, "c1")

    commit(config, "c1")
    unused = harness.send("implemented", commit="HEAD", notes="unused")
    assert unused["status"] == "rejected"
    assert "notes: Extra inputs are not permitted" in unused["message"]
    assert load_record("c1", config=config).status == "drafted"
    missing = harness.send("implemented", commit="no-such-ref")
    assert missing["status"] == "rejected"
    assert harness.send("implemented", commit="HEAD")["status"] == "accepted"

    path = config.root / "runs.csv"
    tracked_run = successful_run(config, "c1")
    path.write_text(HEADER + f"r1,c1,{'0' * 40},0.5\n")
    wrong = harness.send("launched", run_id="r1")
    assert wrong["status"] == "rejected"
    assert "another commit" in wrong["message"]

    # Entering analyze needs metrics, so the launch report waits for them.
    path.write_text(HEADER + f"r1,c1,{tracked_run.commit},\n")
    early = harness.send("launched", run_id="r1")
    assert early["status"] == "not_ready"
    assert "no metrics yet" in early["message"]
    assert harness.section() == "## Launch"


@pytest.mark.parametrize("record_id", ["experiment one", "experiment;echo unsafe"])
def test_collaborative_quotes_launch_arguments(config, record_id):
    harness = Harness(config)
    start_collaborative(harness)
    assert draft(harness, record_id)["status"] == "accepted"
    sha = commit(config, "implementation")
    assert harness.send("implemented", commit=sha)["status"] == "accepted"
    assert shlex.split(harness.instruction["data"]["command"]) == [
        "hiraku",
        "run",
        "--record",
        record_id,
        "--",
        "python",
        "train.py",
    ]


def test_collaborative_uses_only_requested_tracking_run(config):
    harness = Harness(config)
    start_collaborative(harness)
    draft(harness, "c1")
    sha = commit(config, "implementation")
    harness.send("implemented", commit=sha)
    path = config.root / "runs.csv"
    path.write_text(HEADER + f"other,c1,{'0' * 40},0.9\n")
    assert harness.send("launched", run_id="ready")["status"] == "not_ready"
    path.write_text(HEADER + f"other,c1,{'0' * 40},0.9\n" + f"pending,c1,{sha},\n")
    waiting = harness.send("launched", run_id="pending")
    assert waiting["status"] == "not_ready"
    assert "no metrics yet" in waiting["message"]
    path.write_text(HEADER + f"other,c1,{'0' * 40},0.9\n" + f"ready,c1,{sha},0.8\n")
    assert harness.send("launched", run_id="ready")["status"] == "accepted"
    assert harness.instruction["data"]["runs"] == [
        {"run_id": "ready", "metrics": {"val": "0.8"}}
    ]


def test_collaborative_draws_its_experiment_loop(config: HirakuConfig) -> None:
    assert load_workflow("collaborative", config=config).graph.to_mermaid() == (
        "stateDiagram-v2\n"
        "  [*] --> draft\n"
        "  draft --> implement: drafted\n"
        "  implement --> launch: implemented\n"
        "  launch --> analyze: launched\n"
        "  analyze --> draft: analyzed\n"
        "  analyze --> finished: analyzed\n"
        "  draft --> draft: revise_draft\n"
        "  implement --> implement: revise_implementation\n"
        "  launch --> launch: revise_launch\n"
        "  analyze --> analyze: revise_analysis\n"
        "  draft --> finished: finish\n"
        "  implement --> finished: finish\n"
        "  launch --> finished: finish\n"
        "  analyze --> finished: finish\n"
        "  finished --> [*]\n"
    )
