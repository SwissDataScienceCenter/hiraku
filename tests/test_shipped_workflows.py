from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hiraku.config import HirakuConfig
from hiraku.record import Record
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


def test_collaborative_gates_and_budget(config: HirakuConfig) -> None:
    harness = Harness(config)
    started = harness.start("collaborative", task="task", rounds=2)
    assert started["status"] == "accepted"
    assert harness.section() == "## Propose"
    assert harness.replies == ["proposed", "finish"]

    experiment(config, "c1")
    harness.reply("proposed", record_id="c1")
    assert harness.section() == "## Approve"
    assert harness.instruction["requires_user_response"] is True
    assert harness.instruction["context"]["record_id"] == "c1"
    assert harness.replies == [
        "approve",
        "revise",
        "finish",
    ]

    harness.reply("revise", feedback="Try a smaller model")
    assert harness.section() == "## Propose"
    assert harness.instruction["context"]["feedback"] == "Try a smaller model"
    experiment(config, "c2")
    harness.reply("proposed", record_id="c2")
    harness.reply("approve")
    assert harness.section() == "## Run"
    assert harness.instruction["context"]["command"] == (
        "hiraku launch train --record c2"
    )

    pending = harness.reply("submit_run", run_id="r2")
    assert pending["status"] == "not_ready"
    add_run(config, "r2", "c2", "0.8")
    harness.reply("submit_run", run_id="r2")
    assert harness.section() == "## Discuss"
    assert harness.instruction["requires_user_response"] is True
    assert harness.instruction["context"]["result"]["metrics"] == {"val": "0.8"}
    assert harness.replies == ["next_round", "finish"]

    harness.reply("next_round")
    experiment(config, "c3")
    harness.reply("proposed", record_id="c3")
    harness.reply("approve")
    add_run(config, "r3", "c3", "0.7")
    harness.reply("submit_run", run_id="r3")
    assert harness.replies == ["finish"]

    done = harness.reply("finish")
    assert done["completed"] is True
    assert [item["run_id"] for item in done["result"]["results"]] == ["r2", "r3"]


def test_collaborative_rejects_invalid_input(config: HirakuConfig) -> None:
    harness = Harness(config)
    unknown = harness.start("collaborative", task="task", entrypoint="nope")
    assert unknown["status"] == "rejected"
    assert "nope" in unknown["message"]

    harness.start("collaborative", task="task")
    Record.create("other", type="experiment", activate=False, config=config)
    outside = harness.reply("proposed", record_id="other")
    assert outside["status"] == "rejected"
    assert "must be an experiment under 'task'" in outside["message"]

    harness.reply("proposed", record_id="base")
    harness.reply("approve")
    elsewhere = harness.reply("submit_run", run_id="x")
    assert elsewhere["status"] == "not_ready"
    add_run(config, "x", "other", "0.5")
    assert harness.reply("submit_run", run_id="x")["status"] == ("not_ready")


def dataset(config: HirakuConfig, record_id: str, **fields: Any) -> None:
    values = {"type": "dataset", "sources": ["https://example.org/data"], **fields}
    Record.create(record_id, parent_id="task", activate=False, config=config, **values)


def test_autodataset_whole_prompt_completion(config: HirakuConfig) -> None:
    harness = Harness(config)
    harness.start("autodataset", task="task", count=2)
    assert harness.section() == "## Instructions"
    assert "## Shortfall" not in harness.instruction["text"]
    assert harness.replies == ["complete", "report_shortfall"]

    dataset(config, "d1")
    dataset(config, "d2", sources=[])
    dataset(config, "d3", type="other")
    too_few = harness.reply("complete", record_ids=["d1"])
    assert "report_shortfall" in too_few["message"]
    no_sources = harness.reply("complete", record_ids=["d1", "d2"])
    assert "'sources'" in no_sources["message"]
    wrong_type = harness.reply("complete", record_ids=["d1", "d3"])
    assert "type 'dataset'" in wrong_type["message"]
    duplicate = harness.reply("complete", record_ids=["d1", "d1"])
    assert "distinct" in duplicate["message"]

    dataset(config, "d4")
    done = harness.reply("complete", record_ids=["d1", "d4"])
    assert done["result"]["outcome"] == "completed"
    assert done["result"]["datasets"] == ["d1", "d4"]
    assert "not verified" in done["result"]["verified"]


def test_autodataset_shortfall_continue_and_partial(config: HirakuConfig) -> None:
    harness = Harness(config)
    harness.start("autodataset", task="task", count=3)
    dataset(config, "d1")
    not_short = harness.reply("report_shortfall", record_ids=["d1", "d1"], reason="x")
    assert not_short["status"] == "rejected"
    harness.reply("report_shortfall", record_ids=["d1"], reason="Rare")
    assert harness.section() == "## Shortfall"
    assert harness.instruction["requires_user_response"] is True
    assert harness.instruction["context"] == {
        "requested": 3,
        "found": ["d1"],
        "reason": "Rare",
    }
    harness.reply("continue_search")
    assert harness.section() == "## Instructions"
    assert harness.instruction["context"]["already_found"] == ["d1"]
    harness.reply("report_shortfall", record_ids=["d1"], reason="Rare")
    done = harness.reply("accept_partial")
    assert done["result"]["outcome"] == "partial"
    assert done["result"]["datasets"] == ["d1"]
