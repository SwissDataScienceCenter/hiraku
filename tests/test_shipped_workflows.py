from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from hiraku.config import HirakuConfig
from hiraku.record import Record, load_record
from hiraku.workflow import load_workflows

HEADER = "id,record_id,entrypoint,metric.val\n"


class Harness:
    """Scripted stand-in for a coding harness driving one session."""

    def __init__(self, config: HirakuConfig) -> None:
        self.config = config
        self.workflows = load_workflows(config)
        self.response: dict[str, Any] = {}

    def start(self, workflow: str, **arguments: Any) -> dict[str, Any]:
        self.workflow = self.workflows[workflow].bind(self.config)
        self.response = self.workflow.run_start(arguments)
        return self.response

    def reply(self, reply: str, **arguments: Any) -> dict[str, Any]:
        result = self.workflow.run_reply(
            self.response["session_id"],
            self.response["instruction_id"],
            reply,
            arguments,
        )
        if result["status"] == "accepted":
            self.response = result
        return result

    @property
    def instruction(self) -> dict[str, Any]:
        return self.response["instruction"]

    @property
    def replies(self) -> list[str]:
        return [item["reply"] for item in self.instruction["replies"]]

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
    return harness.reply(
        "drafted",
        record_id=record_id,
        summary=summary,
        hypothesis="Dropout reduces overfitting",
        change="Add dropout 0.3 before the head",
    )


def run_experiment(harness: Harness, config: HirakuConfig, record_id: str) -> str:
    """Take a drafted experiment through implement, launch, and analyze."""
    harness.reply("implement")
    sha = commit(config, record_id)
    harness.reply("implemented", commit="HEAD", notes="One line")
    harness.reply("launch")
    add_run(config, f"r-{record_id}", record_id, "0.8")
    harness.reply("launched")
    harness.reply("analyze")
    harness.reply("analyzed", verdict="supports", conclusion="Better")
    return sha


def test_collaborative_steps_reviews_and_budget(config: HirakuConfig) -> None:
    harness = Harness(config)
    started = harness.start("collaborative", task="task", rounds=2)
    assert started["status"] == "accepted"
    assert harness.section() == "## Draft"
    assert harness.instruction["text"].endswith(
        "Never delete records or rewrite Git history."
    )
    assert harness.replies == ["drafted", "finish"]

    draft(harness, "c1")
    assert harness.section() == "## Review"
    assert "Never delete records" not in harness.instruction["text"]
    assert harness.instruction["requires_user_response"] is True
    assert harness.instruction["context"]["result"]["record_id"] == "c1"
    assert harness.replies == ["implement", "revise", "finish"]
    assert load_record("c1", config=config).status == "drafted"

    harness.reply("revise", feedback="Try weight decay instead")
    assert harness.section() == "## Draft"
    assert harness.instruction["context"]["feedback"] == "Try weight decay instead"
    assert harness.instruction["context"]["revise"] == "c1"
    elsewhere = draft(harness, "c9")
    assert elsewhere["status"] == "rejected"
    assert "record_id 'c1'" in elsewhere["message"]
    draft(harness, "c1", summary="Add weight decay")
    assert load_record("c1", config=config).summary == "Add weight decay"

    harness.reply("implement")
    assert harness.section() == "## Implement"
    assert harness.instruction["context"]["experiment"]["summary"] == (
        "Add weight decay"
    )
    sha = commit(config, "weight decay")
    harness.reply("implemented", commit="HEAD", notes="One line")
    assert load_record("c1", config=config).commit == sha
    assert harness.replies == ["launch", "revise", "finish"]

    harness.reply("launch")
    assert harness.instruction["context"]["command"] == (
        f"hiraku launch train --record c1 --commit {sha}"
    )
    assert harness.reply("launched")["status"] == "not_ready"
    add_run(config, "r1", "c1", "0.8")
    harness.reply("launched")
    assert harness.instruction["context"]["result"]["runs"] == [
        {"run_id": "r1", "metrics": {"val": "0.8"}}
    ]
    assert harness.replies == ["analyze", "revise", "finish"]

    harness.reply("analyze")
    assert harness.section() == "## Analyze"
    assert harness.instruction["context"]["runs"][0]["run_id"] == "r1"
    harness.reply("analyzed", verdict="inconclusive", conclusion="Unclear")
    harness.reply("revise", feedback="Compare with the baseline")
    harness.reply("analyzed", verdict="supports", conclusion="Better")
    record = load_record("c1", config=config)
    assert (record.status, record.verdict) == ("analyzed", "supports")
    assert harness.instruction["context"]["rounds_left"] == 1
    assert harness.replies == ["draft", "revise", "finish"]

    harness.reply("draft")
    assert harness.instruction["context"]["previous"] == [
        {"record_id": "c1", "verdict": "supports"}
    ]
    draft(harness, "c2")
    run_experiment(harness, config, "c2")
    assert harness.replies == ["revise", "finish"]

    done = harness.reply("finish")
    assert done["completed"] is True
    assert harness.section() == "## Done"
    assert [
        item["record_id"] for item in done["instruction"]["context"]["results"]
    ] == [
        "c1",
        "c2",
    ]


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

    harness.reply("implement")
    commit(config, "c1")
    missing = harness.reply("implemented", commit="no-such-ref", notes="x")
    assert missing["status"] == "rejected"
    harness.reply("implemented", commit="HEAD", notes="x")

    harness.reply("launch")
    path = config.root / "runs.csv"
    path.write_text("id,record_id,commit,metric.val\nr1,c1," + "0" * 40 + ",0.5\n")
    wrong = harness.reply("launched")
    assert wrong["status"] == "rejected"
    assert "another commit" in wrong["message"]

    path.write_text("id,record_id,metric.val\nr1,c1,\n")
    harness.reply("launched")
    early = harness.reply("analyze")
    assert early["status"] == "not_ready"
    assert "no metrics yet" in early["message"]
