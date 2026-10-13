from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hiraku.config import HirakuConfig
from hiraku.record import Record, load_record, update_record
from hiraku.workflow import load_workflows, split_tool_name

COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
HEADER = "id,record_id,commit,git_state,entrypoint,status,metric.val,param.lr\n"


class Harness:
    """Scripted stand-in for a coding harness driving one session."""

    def __init__(self, config: HirakuConfig) -> None:
        self.config = config
        self.workflows = load_workflows(config)
        self.response: dict[str, Any] = {}

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        workflow_id, tool = split_tool_name(name)
        return self.workflows[workflow_id].bind(self.config).call(tool, arguments)

    def start(self, workflow: str, **arguments: Any) -> dict[str, Any]:
        self.workflow = workflow
        self.response = self.call(f"{workflow}_start", arguments)
        return self.response

    def reply(self, tool: str, **arguments: Any) -> dict[str, Any]:
        result = self.call(
            tool,
            {
                "session_id": self.response["session_id"],
                "instruction_id": self.response["instruction_id"],
                **arguments,
            },
        )
        if result["status"] == "accepted":
            self.response = result
        return result

    @property
    def instruction(self) -> dict[str, Any]:
        return self.response["instruction"]

    @property
    def replies(self) -> list[str]:
        return [
            f"{self.workflow}_{item['reply']}" for item in self.instruction["replies"]
        ]

    def section(self) -> str:
        return self.instruction["text"].splitlines()[0]


def experiment(config: HirakuConfig, record_id: str, commit: str | None = None) -> None:
    fields = {"summary": f"Try {record_id}", "hypothesis": "It helps"}
    if commit is not None:
        fields["commit"] = commit
    Record.create(
        record_id,
        parent_id="task",
        type="experiment",
        activate=False,
        config=config,
        **fields,
    )


def add_run(
    config: HirakuConfig,
    run_id: str,
    record_id: str,
    value: str,
    *,
    commit: str = COMMIT_A,
    status: str = "finished",
    git_state: str = "clean",
    entrypoint: str = "train",
    lr: str = "",
) -> None:
    path = config.root / "runs.csv"
    if not path.exists():
        path.write_text(HEADER)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            f"{run_id},{record_id},{commit},{git_state},{entrypoint},{status},"
            f"{value},{lr}\n"
        )


@pytest.fixture
def config(tmp_path: Path) -> HirakuConfig:
    selected = HirakuConfig.init(tmp_path, install_skill=False, install_mcp=False)
    Record.create(
        "task",
        type="task",
        config=selected,
        metrics=[{"name": "val", "direction": "minimize"}],
        entrypoints={
            "train": {"command": ["python", "train.py"]},
            "mock": {"command": ["python", "mock.py"]},
        },
    )
    experiment(selected, "base", COMMIT_A)
    add_run(selected, "r0", "base", "1.0")
    return selected


def recorded(config: HirakuConfig, record_id: str, decision: str) -> None:
    update_record(
        record_id,
        changes={"decision": decision, "conclusion": f"{record_id}: {decision}"},
        config=config,
    )


def test_start_resolves_task_entrypoint_and_metric(config: HirakuConfig) -> None:
    harness = Harness(config)
    ambiguous = harness.start("autoresearch-python")
    assert ambiguous["status"] == "rejected"
    assert "several entrypoints (mock, train); pass entrypoint" in ambiguous["message"]
    assert (
        harness.start("autoresearch-python", entrypoint="nope")["status"] == "rejected"
    )
    assert harness.start("autoresearch-python", iterations=0)["status"] == "rejected"
    config.set_active_record("base")
    started = harness.start(
        "autoresearch-python", entrypoint="train", baseline_run="r0"
    )
    assert started["status"] == "accepted"
    assert harness.section() == "## Experiment"
    assert harness.instruction["requires_user_response"] is False
    context = harness.instruction["context"]
    assert context["task"] == "task"
    assert context["reference"]["run_id"] == "r0"
    assert context["experiment"] == 1
    rejected = harness.start("autoresearch-python", task="base", entrypoint="train")
    assert "not a task record" in rejected["message"]


def test_autoresearch_loop_keeps_discards_and_crashes(config: HirakuConfig) -> None:
    harness = Harness(config)
    harness.start("autoresearch-python", task="task", entrypoint="train", iterations=3)
    assert harness.section() == "## Baseline"
    assert harness.replies == [
        "autoresearch-python_submit_run",
        "autoresearch-python_stop",
    ]

    experiment(config, "fresh-base", COMMIT_A)
    assert harness.reply("autoresearch-python_submit_run", run_id="rb")["status"] == (
        "not_ready"
    )
    add_run(config, "rb", "fresh-base", "", status="crashed")
    harness.reply("autoresearch-python_submit_run", run_id="rb")
    assert harness.section() == "## Record"
    assert harness.instruction["context"]["decision"] == "crash"
    assert harness.instruction["context"]["restore_command"] is None
    recorded(config, "fresh-base", "crash")
    harness.reply("autoresearch-python_recorded")
    assert harness.section() == "## Baseline"

    experiment(config, "base-2", COMMIT_A)
    add_run(config, "rb2", "base-2", "1.0")
    harness.reply("autoresearch-python_submit_run", run_id="rb2")
    assert harness.instruction["context"]["decision"] == "keep"
    unrecorded = harness.reply("autoresearch-python_recorded")
    assert "decision: keep" in unrecorded["message"]
    recorded(config, "base-2", "keep")
    harness.reply("autoresearch-python_recorded")
    assert harness.section() == "## Experiment"
    assert harness.instruction["context"]["experiment"] == 1

    experiment(config, "e1", COMMIT_B)
    add_run(config, "r1", "e1", "0.8", commit=COMMIT_B)
    harness.reply("autoresearch-python_submit_run", run_id="r1")
    context = harness.instruction["context"]
    assert context["decision"] == "keep"
    assert context["restore_command"] is None
    assessment = load_record("e1", config=config).to_dict()["assessment"]
    assert assessment["reference_run"] == "rb2"
    assert assessment["value"] == 0.8
    recorded(config, "e1", "keep")
    harness.reply("autoresearch-python_recorded")
    assert harness.instruction["context"]["reference"]["run_id"] == "r1"

    experiment(config, "e2", COMMIT_A)
    add_run(config, "r2", "e2", "0.8")
    harness.reply("autoresearch-python_submit_run", run_id="r2")
    assert harness.instruction["context"]["decision"] == "discard"
    assert harness.instruction["context"]["restore_command"] == (
        f"git reset --hard {COMMIT_B}"
    )
    recorded(config, "e2", "discard")
    harness.reply("autoresearch-python_recorded")

    reused = harness.reply("autoresearch-python_submit_run", run_id="r2")
    assert "already assessed" in reused["message"]
    experiment(config, "e3", COMMIT_A)
    add_run(config, "r3", "e3", "", status="timeout")
    harness.reply("autoresearch-python_submit_run", run_id="r3")
    assert harness.instruction["context"]["decision"] == "crash"
    assert harness.instruction["context"]["restore_command"] == (
        f"git reset --hard {COMMIT_B}"
    )
    recorded(config, "e3", "crash")
    done = harness.reply("autoresearch-python_recorded")
    result = done["result"]
    assert result["outcome"] == "completed"
    assert result["experiments"] == 3
    assert result["best"]["run_id"] == "r1"
    assert [(item["record_id"], item["decision"]) for item in result["results"]] == [
        ("fresh-base", "crash"),
        ("base-2", "keep"),
        ("e1", "keep"),
        ("e2", "discard"),
        ("e3", "crash"),
    ]


@pytest.mark.parametrize(
    ("run", "issue"),
    [
        ({"git_state": "dirty"}, "dirty working tree"),
        ({"status": ""}, "no execution status"),
        ({"value": "nan"}, "no finite"),
        ({"commit": COMMIT_B}, "commit"),
        ({"entrypoint": "mock"}, "entrypoint 'mock'"),
    ],
)
def test_autoresearch_rejects_invalid_evidence(
    config: HirakuConfig, run: dict[str, str], issue: str
) -> None:
    harness = Harness(config)
    harness.start(
        "autoresearch-python", task="task", entrypoint="train", baseline_run="r0"
    )
    experiment(config, "e1", COMMIT_A)
    add_run(config, "r1", "e1", **{"value": "0.5", **run})
    rejected = harness.reply("autoresearch-python_submit_run", run_id="r1")
    assert rejected["status"] == "rejected"
    assert issue in rejected["message"]
    assert harness.section() == "## Experiment"


def test_autoresearch_rejects_runs_outside_the_task(config: HirakuConfig) -> None:
    harness = Harness(config)
    harness.start(
        "autoresearch-python", task="task", entrypoint="train", baseline_run="r0"
    )
    Record.create("elsewhere", type="experiment", activate=False, config=config)
    add_run(config, "r1", "elsewhere", "0.5")
    rejected = harness.reply("autoresearch-python_submit_run", run_id="r1")
    assert "not an experiment record under task" in rejected["message"]


def test_autoresearch_stop(config: HirakuConfig) -> None:
    harness = Harness(config)
    harness.start(
        "autoresearch-python", task="task", entrypoint="train", baseline_run="r0"
    )
    done = harness.reply("autoresearch-python_stop", reason="Out of time")
    assert done["result"]["outcome"] == "stopped"
    assert done["result"]["experiments"] == 0


def guided(harness: Harness, config: HirakuConfig) -> None:
    harness.start("guided-research", task="task", entrypoint="train", candidates=2)
    experiment(config, "g1")
    experiment(config, "g2")
    harness.reply("guided-research_proposed", record_ids=["g1", "g2"])


def test_guided_research_gates_and_result(config: HirakuConfig) -> None:
    harness = Harness(config)
    harness.start("guided-research", task="task", entrypoint="train", candidates=2)
    assert harness.section() == "## Propose"
    experiment(config, "g1")
    experiment(config, "g2")
    assert harness.reply("guided-research_proposed", record_ids=["g1"])["status"] == (
        "rejected"
    )
    harness.reply("guided-research_proposed", record_ids=["g1", "g2"])
    assert harness.section() == "## Choose"
    assert harness.instruction["requires_user_response"] is True

    harness.reply("guided-research_revise", feedback="Try regularization")
    assert harness.instruction["context"]["feedback"] == "Try regularization"
    again = harness.reply("guided-research_proposed", record_ids=["g1", "g2"])
    assert "already proposed" in again["message"]
    experiment(config, "g3")
    experiment(config, "g4")
    harness.reply("guided-research_proposed", record_ids=["g3", "g4"])
    harness.reply("guided-research_select", record_id="g3")
    assert harness.section() == "## Implement"
    assert (
        "Bind the implementation commit"
        in harness.reply("guided-research_implemented")["message"]
    )

    update_record("g3", changes={"commit": COMMIT_A}, config=config)
    harness.reply("guided-research_implemented")
    assert harness.section() == "## Approve"
    assert harness.instruction["requires_user_response"] is True
    assert harness.instruction["context"]["command"] == (
        f"hiraku launch train --record g3 --commit {COMMIT_A}"
    )
    assert harness.replies == ["guided-research_approve", "guided-research_skip"]
    harness.reply("guided-research_approve")
    assert harness.section() == "## Launch"

    assert harness.reply("guided-research_submit_run", run_id="x")["status"] == (
        "not_ready"
    )
    add_run(config, "x", "g3", "0.9", commit=COMMIT_B)
    mismatch = harness.reply("guided-research_submit_run", run_id="x")
    assert "not the approved launch" in mismatch["message"]
    add_run(config, "y", "g3", "0.9")
    harness.reply("guided-research_submit_run", run_id="y")
    assert harness.section() == "## Discuss"
    assert harness.instruction["requires_user_response"] is True
    assert harness.instruction["context"]["result"]["outcome"] == "first_result"
    unconcluded = harness.reply("guided-research_next_round")
    assert "Write the conclusion" in unconcluded["message"]
    update_record("g3", changes={"conclusion": "Baseline-like"}, config=config)
    harness.reply("guided-research_next_round")
    assert harness.section() == "## Propose"
    assert harness.instruction["context"]["best"]["run_id"] == "y"

    experiment(config, "g5", COMMIT_A)
    experiment(config, "g6")
    harness.reply("guided-research_proposed", record_ids=["g5", "g6"])
    harness.reply("guided-research_select", record_id="g5")
    harness.reply("guided-research_implemented")
    harness.reply("guided-research_approve")
    add_run(config, "z", "g5", "0.7")
    harness.reply("guided-research_submit_run", run_id="z")
    assert harness.instruction["context"]["result"]["outcome"] == "improved"
    update_record("g5", changes={"conclusion": "Better"}, config=config)
    done = harness.reply("guided-research_finish")
    assert done["result"]["best"]["run_id"] == "z"
    assert [item["outcome"] for item in done["result"]["outcomes"]] == [
        "first_result",
        "improved",
    ]


def test_guided_research_approval_is_prospective(config: HirakuConfig) -> None:
    harness = Harness(config)
    guided(harness, config)
    harness.reply("guided-research_select", record_id="g1")
    update_record("g1", changes={"commit": COMMIT_A}, config=config)
    harness.reply("guided-research_implemented")
    harness.reply("guided-research_approve")
    update_record(
        "g1",
        changes={
            "entrypoints": {
                "train": {"command": ["python", "train.py"], "args": {"lr": 0.5}}
            }
        },
        config=config,
    )
    add_run(config, "r1", "g1", "0.5", lr="0.5")
    changed = harness.reply("guided-research_submit_run", run_id="r1")
    assert changed["status"] == "accepted"
    assert harness.section() == "## Approve"
    assert harness.instruction["context"]["arguments"] == {"lr": 0.5}


def test_guided_research_skip_and_crash(config: HirakuConfig) -> None:
    harness = Harness(config)
    guided(harness, config)
    harness.reply("guided-research_select", record_id="g1")
    harness.reply("guided-research_skip", reason="Too expensive")
    assert harness.section() == "## Propose"
    experiment(config, "g3", COMMIT_A)
    experiment(config, "g4")
    harness.reply("guided-research_proposed", record_ids=["g3", "g4"])
    harness.reply("guided-research_select", record_id="g3")
    harness.reply("guided-research_implemented")
    harness.reply("guided-research_approve")
    add_run(config, "c1", "g3", "", status="crashed")
    harness.reply("guided-research_submit_run", run_id="c1")
    assert harness.instruction["context"]["result"]["outcome"] == "crashed"
    update_record("g3", changes={"conclusion": "OOM"}, config=config)
    done = harness.reply("guided-research_finish")
    assert [item["outcome"] for item in done["result"]["outcomes"]] == [
        "skipped",
        "crashed",
    ]
    assert done["result"]["best"] is None


def dataset(config: HirakuConfig, record_id: str, **fields: Any) -> None:
    values = {"type": "dataset", "sources": ["https://example.org/data"], **fields}
    Record.create(record_id, parent_id="task", activate=False, config=config, **values)


def test_autodataset_whole_prompt_completion(config: HirakuConfig) -> None:
    harness = Harness(config)
    harness.start("autodataset", task="task", count=2)
    assert harness.section() == "## Instructions"
    assert "## Shortfall" not in harness.instruction["text"]
    assert harness.replies == ["autodataset_complete", "autodataset_report_shortfall"]

    dataset(config, "d1")
    dataset(config, "d2", sources=[])
    dataset(config, "d3", type="other")
    too_few = harness.reply("autodataset_complete", record_ids=["d1"])
    assert "report_shortfall" in too_few["message"]
    no_sources = harness.reply("autodataset_complete", record_ids=["d1", "d2"])
    assert "'sources'" in no_sources["message"]
    wrong_type = harness.reply("autodataset_complete", record_ids=["d1", "d3"])
    assert "type 'dataset'" in wrong_type["message"]
    duplicate = harness.reply("autodataset_complete", record_ids=["d1", "d1"])
    assert "distinct" in duplicate["message"]

    dataset(config, "d4")
    done = harness.reply("autodataset_complete", record_ids=["d1", "d4"])
    assert done["result"]["outcome"] == "completed"
    assert done["result"]["datasets"] == ["d1", "d4"]
    assert "not verified" in done["result"]["verified"]


def test_autodataset_shortfall_continue_and_partial(config: HirakuConfig) -> None:
    harness = Harness(config)
    harness.start("autodataset", task="task", count=3)
    dataset(config, "d1")
    not_short = harness.reply(
        "autodataset_report_shortfall", record_ids=["d1", "d1"], reason="x"
    )
    assert not_short["status"] == "rejected"
    harness.reply("autodataset_report_shortfall", record_ids=["d1"], reason="Rare")
    assert harness.section() == "## Shortfall"
    assert harness.instruction["requires_user_response"] is True
    assert harness.instruction["context"] == {
        "requested": 3,
        "found": ["d1"],
        "reason": "Rare",
    }
    harness.reply("autodataset_continue_search")
    assert harness.section() == "## Instructions"
    assert harness.instruction["context"]["already_found"] == ["d1"]
    harness.reply("autodataset_report_shortfall", record_ids=["d1"], reason="Rare")
    done = harness.reply("autodataset_accept_partial")
    assert done["result"]["outcome"] == "partial"
    assert done["result"]["datasets"] == ["d1"]
