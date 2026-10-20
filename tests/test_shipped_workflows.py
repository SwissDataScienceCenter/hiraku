from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from hiraku.config import HirakuConfig
from hiraku.record import Record, load_record
from hiraku.workflows.workflow import list_workflows, load_workflow

HEADER = "id,record_id,entrypoint,metric.val\n"


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
        result = self.workflow.run_start(arguments)
        self.response = self.deliver(result)
        return self.response

    def send(self, event: str, **arguments: Any) -> dict[str, Any]:
        result = self.workflow.run_event(
            self.response["session_id"],
            self.response["instruction_id"],
            event,
            arguments,
        )
        result = self.deliver(result)
        if result["status"] == "accepted":
            self.response = result
        return result

    def deliver(self, result):
        response = result.model_dump(exclude_none=True)
        if result.status == "accepted":
            response.update(self.workflow.get_instruction(result.session_id))
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


def add_run(config: HirakuConfig, run_id: str, record_id: str, value: str) -> None:
    path = config.root / "runs.csv"
    if not path.exists():
        path.write_text(HEADER)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"{run_id},{record_id},train,{value}\n")


@pytest.fixture
def config(tmp_path: Path) -> HirakuConfig:
    selected = HirakuConfig.init(tmp_path, install_skill=False)
    Record.create(
        "task",
        type="task",
        config=selected,
        entrypoints={"train": {"command": ["python", "train.py"]}},
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
    (config.root / "train.py").write_text(f"# {message}\n")
    git("add", "train.py")
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
    add_run(config, f"r-{record_id}", record_id, "0.8")
    harness.send("launched")
    harness.send("analyzed", verdict="supports", conclusion="Better")
    return sha


def test_collaborative_runs_each_step_and_stops_after_the_rounds(
    config: HirakuConfig,
) -> None:
    harness = Harness(config)
    started = harness.start("collaborative", task="task", rounds=2)
    assert started["status"] == "accepted"
    assert harness.section() == "## Draft"
    # Until gates exist, the agent gets the user's approval in the conversation.
    text = " ".join(harness.instruction["text"].split())
    assert "Once they approve, send the event `drafted`" in text
    assert harness.instruction["text"].endswith(
        "Never delete records or rewrite Git history."
    )
    assert harness.instruction["context"] == {"task": "task", "previous": []}
    assert harness.events == ["drafted", "revise_draft", "finish"]

    # revise redoes the current step with the user's feedback.
    harness.send("revise_draft", feedback="Try weight decay instead")
    assert harness.section() == "## Draft"
    assert harness.instruction["context"]["feedback"] == "Try weight decay instead"
    accepted = harness.instruction["events"]["accepted"]
    assert (
        accepted[1]["description"] == "Redraft the experiment with the user's feedback."
    )

    draft(harness, "c1")
    assert harness.section() == "## Implement"
    assert harness.instruction["context"]["experiment"]["summary"] == "Add dropout"
    assert "feedback" not in harness.instruction["context"]
    assert harness.events == ["implemented", "revise_implementation", "finish"]
    harness.send("revise_implementation", feedback="Keep the learning rate")
    assert harness.section() == "## Implement"
    assert harness.instruction["context"]["feedback"] == "Keep the learning rate"
    accepted = harness.instruction["events"]["accepted"]
    assert accepted[1]["description"] == (
        "Rework the implementation with the user's feedback."
    )
    assert load_record("c1", config=config).status == "drafted"

    sha = commit(config, "dropout")
    harness.send("implemented", commit="HEAD")
    assert load_record("c1", config=config).commit == sha
    assert harness.section() == "## Launch"
    assert harness.instruction["context"]["command"] == (
        f"hiraku launch train --record c1 --commit {sha}"
    )
    assert harness.events == ["launched", "revise_launch", "finish"]
    harness.send("revise_launch", feedback="Use the local dispatcher")
    assert harness.instruction["context"]["feedback"] == "Use the local dispatcher"

    assert harness.send("launched")["status"] == "not_ready"
    add_run(config, "r1", "c1", "0.8")
    harness.send("launched")
    assert harness.section() == "## Analyze"
    assert harness.instruction["context"]["runs"] == [
        {"run_id": "r1", "metrics": {"val": "0.8"}}
    ]
    assert harness.events == ["analyzed", "revise_analysis", "finish"]
    harness.send("revise_analysis", feedback="Compare against the baseline")
    assert harness.instruction["context"]["feedback"] == "Compare against the baseline"

    harness.send("analyzed", verdict="supports", conclusion="Better")
    record = load_record("c1", config=config)
    assert (record.status, record.verdict) == ("analyzed", "supports")
    assert harness.section() == "## Draft"
    assert harness.instruction["context"]["previous"] == [
        {"record_id": "c1", "verdict": "supports"}
    ]

    draft(harness, "c2")
    run_experiment(harness, config, "c2")
    assert harness.response["session_status"] == "completed"
    assert harness.section() == "## Done"
    results = harness.instruction["context"]["results"]
    assert [item["record_id"] for item in results] == ["c1", "c2"]


def test_collaborative_can_finish_from_any_step(config: HirakuConfig) -> None:
    harness = Harness(config)
    harness.start("collaborative", task="task")
    draft(harness, "c1")
    done = harness.send("finish")
    assert done["session_status"] == "completed"
    assert done["instruction"]["context"] == {"task": "task", "results": []}


def test_collaborative_rejects_invalid_input(config: HirakuConfig) -> None:
    harness = Harness(config)
    unknown = harness.start("collaborative", task="task", entrypoint="nope")
    assert unknown["status"] == "rejected"
    assert "nope" in unknown["message"]

    harness.start("collaborative", task="task")
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
    path.write_text("id,record_id,commit,metric.val\nr1,c1," + "0" * 40 + ",0.5\n")
    wrong = harness.send("launched")
    assert wrong["status"] == "rejected"
    assert "another commit" in wrong["message"]

    # Entering analyze needs metrics, so the launch report waits for them.
    path.write_text("id,record_id,metric.val\nr1,c1,\n")
    early = harness.send("launched")
    assert early["status"] == "not_ready"
    assert "no metrics yet" in early["message"]
    assert harness.section() == "## Launch"


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
