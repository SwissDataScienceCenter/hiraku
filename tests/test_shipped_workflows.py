from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hiraku.config import HirakuConfig
from hiraku.record import Record, load_record, update_record
from hiraku.workflow.service import WorkflowService

COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
HEADER = "id,record_id,commit,git_state,entrypoint,status,metric.val,param.lr\n"


class Harness:
    """Scripted stand-in for a coding harness driving one session."""

    def __init__(self, config: HirakuConfig) -> None:
        self.config = config
        self.service = WorkflowService(config)
        self.response: dict[str, Any] = {}

    def start(self, workflow: str, **arguments: Any) -> dict[str, Any]:
        self.response = self.service.call(f"{workflow}_start", arguments)
        return self.response

    def reply(self, tool: str, **arguments: Any) -> dict[str, Any]:
        result = self.service.call(
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
        return [item["tool"] for item in self.instruction["replies"]]

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


def test_start_resolves_task_entrypoint_and_metric(config: HirakuConfig) -> None:
    harness = Harness(config)
    ambiguous = harness.start("autoresearch")
    assert ambiguous["status"] == "rejected"
    assert "several entrypoints (mock, train); pass entrypoint" in ambiguous["message"]
    assert harness.start("autoresearch", entrypoint="nope")["status"] == "rejected"
    assert harness.start("autoresearch", iterations=0)["status"] == "rejected"
    config.set_active_record("base")
    started = harness.start("autoresearch", entrypoint="train", baseline_run="r0")
    assert started["status"] == "accepted"
    assert harness.section() == "## Generate"
    context = harness.instruction["context"]
    assert context["task"] == "task"
    assert context["reference"] == {"run_id": "r0", "record_id": "base", "value": 1.0}
    assert "Ground rules:" in started["orientation"]
    rejected = harness.start("autoresearch", task="base", entrypoint="train")
    assert "not a task record" in rejected["message"]


def test_collaborative_iteration_with_revision_approval_and_review(
    config: HirakuConfig,
) -> None:
    harness = Harness(config)
    harness.start(
        "autoresearch",
        task="task",
        entrypoint="train",
        iterations=2,
        candidates=2,
        baseline_run="r0",
    )
    assert harness.instruction["context"]["count"] == 2

    experiment(config, "e1")
    experiment(config, "e2")
    Record.create("not-exp", parent_id="task", activate=False, config=config)
    assert harness.reply("autoresearch_proposed", record_ids=["e1"])["status"] == (
        "rejected"
    )
    wrong_type = harness.reply("autoresearch_proposed", record_ids=["e1", "not-exp"])
    assert "type 'experiment'" in wrong_type["message"]
    assert harness.reply("autoresearch_proposed", record_ids=["e1", "e2"])[
        "status"
    ] == ("accepted")
    assert harness.section() == "## Select"
    assert harness.instruction["requires_user_response"] is True
    assert [item["id"] for item in harness.instruction["context"]["candidates"]] == [
        "e1",
        "e2",
    ]

    harness.reply("autoresearch_revise", feedback="Be bolder")
    assert harness.section() == "## Generate"
    assert harness.instruction["context"]["feedback"] == "Be bolder"
    reused = harness.reply("autoresearch_proposed", record_ids=["e1", "e3"])
    assert "already proposed" in reused["message"]
    experiment(config, "e3")
    experiment(config, "e4")
    harness.reply("autoresearch_proposed", record_ids=["e3", "e4"])
    assert harness.reply("autoresearch_select", record_id="e9")["status"] == (
        "rejected"
    )
    harness.reply("autoresearch_select", record_id="e3")
    assert harness.section() == "## Implement"

    unbound = harness.reply("autoresearch_implemented")
    assert "no bound commit" in unbound["message"]
    update_record("e3", changes={"commit": COMMIT_A}, config=config)
    harness.reply("autoresearch_implemented", arguments={"lr": 0.1})
    assert harness.section() == "## Approve"
    assert harness.instruction["requires_user_response"] is True
    assert harness.instruction["context"]["command"] == (
        f"hiraku launch train --record e3 --commit {COMMIT_A} -- --lr 0.1"
    )
    assert "autoresearch_submit_run" not in harness.replies

    harness.reply("autoresearch_change_configuration", arguments={"lr": 0.2})
    assert harness.section() == "## Approve"
    assert harness.instruction["context"]["arguments"] == {"lr": 0.2}
    harness.reply("autoresearch_approve")
    assert harness.section() == "## Launch"

    pending = harness.reply("autoresearch_submit_run", run_id="r1")
    assert pending["status"] == "not_ready"
    add_run(config, "r1", "e3", "0.8", lr="0.2")
    harness.reply("autoresearch_submit_run", run_id="r1")
    assert harness.section() == "## Conclude"
    assessment = load_record("e3", config=config).to_dict()["assessment"]
    assert assessment["improved"] is True
    assert assessment["value"] == 0.8
    assert assessment["reference_run"] == "r0"
    assert assessment["session"] == harness.response["session_id"]

    missing = harness.reply("autoresearch_concluded")
    assert "no conclusion field" in missing["message"]
    update_record(
        "e3", changes={"conclusion": "Improved", "decision": "keep"}, config=config
    )
    harness.reply("autoresearch_concluded")
    assert harness.section() == "## Generate"
    assert harness.instruction["context"]["iteration"] == {"current": 2, "of": 2}
    assert harness.instruction["context"]["reference"]["run_id"] == "r1"

    experiment(config, "e5", COMMIT_A)
    experiment(config, "e6")
    harness.reply("autoresearch_proposed", record_ids=["e5", "e6"])
    harness.reply("autoresearch_select", record_id="e5")
    harness.reply("autoresearch_implemented")
    harness.reply("autoresearch_approve")
    add_run(config, "r2", "e5", "0.5", commit=COMMIT_B)
    harness.reply("autoresearch_submit_run", run_id="r2")
    assert harness.section() == "## Review"
    assert harness.instruction["requires_user_response"] is True
    assert "commit" in harness.instruction["context"]["issue"]
    assert "autoresearch_approve" not in harness.replies
    assert "autoresearch_skip" in harness.replies

    done = harness.reply("autoresearch_skip", reason="Wrong commit was launched")
    assert done["completed"] is True
    result = done["result"]
    assert result["outcome"] == "completed"
    assert result["iterations"] == {"budget": 2, "used": 2}
    assert result["baseline"]["run_id"] == "r0"
    assert result["best"] == {"run_id": "r1", "record_id": "e3", "value": 0.8}
    assert [(item["record_id"], item["outcome"]) for item in result["attempts"]] == [
        ("e3", "assessed"),
        ("e5", "skipped"),
    ]
    assert result["unresolved"] == ["e5"]


def test_autonomous_baseline_failure_and_failed_attempt(config: HirakuConfig) -> None:
    harness = Harness(config)
    harness.start(
        "autoresearch",
        task="task",
        entrypoint="train",
        iterations=1,
        candidates=1,
        collaborative=False,
    )
    assert harness.section() == "## Baseline"
    assert harness.instruction["requires_user_response"] is False

    experiment(config, "fresh-base", COMMIT_A)
    harness.reply("autoresearch_establish_baseline", record_id="fresh-base")
    assert harness.section() == "## Launch"
    add_run(config, "rb", "fresh-base", "", status="crashed")
    harness.reply("autoresearch_submit_run", run_id="rb")
    assert harness.section() == "## Baseline"
    assert "crashed" in harness.instruction["context"]["issue"]

    add_run(config, "rx", "base", "1.0", entrypoint="mock")
    incomparable = harness.reply("autoresearch_use_baseline", run_id="rx")
    assert "not comparable" in incomparable["message"]
    harness.reply("autoresearch_use_baseline", run_id="r0")
    assert harness.section() == "## Generate"

    experiment(config, "e1", COMMIT_A)
    harness.reply("autoresearch_proposed", record_ids=["e1"])
    assert harness.instruction["requires_user_response"] is False
    unexplained = harness.reply("autoresearch_select", record_id="e1")
    assert "rationale" in unexplained["message"]
    harness.reply("autoresearch_select", record_id="e1", rationale="Most promising")
    assert load_record("e1", config=config).to_dict()["selection_rationale"] == (
        "Most promising"
    )
    harness.reply("autoresearch_implemented")
    assert harness.section() == "## Launch"
    add_run(config, "r1", "e1", "0.0", status="crashed")
    harness.reply("autoresearch_submit_run", run_id="r1")
    assert harness.section() == "## Conclude"
    assert harness.instruction["context"]["assessment"]["status"] == "crashed"
    update_record("e1", changes={"conclusion": "Crashed: OOM"}, config=config)
    done = harness.reply("autoresearch_concluded")
    assert done["result"]["attempts"][0]["outcome"] == "failed"
    assert done["result"]["best"]["run_id"] == "r0"


@pytest.mark.parametrize(
    ("run", "issue"),
    [
        ({"git_state": "dirty"}, "dirty working tree"),
        ({"status": ""}, "no execution status"),
        ({"value": "nan"}, "no finite value"),
        ({"value": ""}, "no finite value"),
        ({"lr": "0.5"}, "tracked lr='0.5'"),
        ({"entrypoint": "mock"}, "entrypoint 'mock'"),
    ],
)
def test_invalid_evidence_needs_review(
    config: HirakuConfig, run: dict[str, str], issue: str
) -> None:
    harness = Harness(config)
    harness.start(
        "autoresearch",
        task="task",
        entrypoint="train",
        iterations=1,
        candidates=1,
        collaborative=False,
        baseline_run="r0",
    )
    experiment(config, "e1", COMMIT_A)
    harness.reply("autoresearch_proposed", record_ids=["e1"])
    harness.reply("autoresearch_select", record_id="e1", rationale="Only one")
    harness.reply("autoresearch_implemented", arguments={"lr": 0.1})
    add_run(config, "r1", "e1", **{"value": "0.5", "lr": "0.1", **run})
    harness.reply("autoresearch_submit_run", run_id="r1")
    assert harness.section() == "## Review"
    assert issue in harness.instruction["context"]["issue"]
    assert harness.instruction["requires_user_response"] is True


def test_equal_result_is_not_an_improvement(config: HirakuConfig) -> None:
    harness = Harness(config)
    harness.start(
        "autoresearch",
        task="task",
        entrypoint="train",
        iterations=1,
        candidates=1,
        collaborative=False,
        baseline_run="r0",
    )
    experiment(config, "e1", COMMIT_A)
    harness.reply("autoresearch_proposed", record_ids=["e1"])
    harness.reply("autoresearch_select", record_id="e1", rationale="Only one")
    harness.reply("autoresearch_implemented")
    add_run(config, "r1", "e1", "1.0")
    harness.reply("autoresearch_submit_run", run_id="r1")
    assert load_record("e1", config=config).to_dict()["assessment"]["improved"] is False
    update_record("e1", changes={"conclusion": "No change"}, config=config)
    done = harness.reply("autoresearch_concluded")
    assert done["result"]["best"]["run_id"] == "r0"


def test_declined_and_failed_launches_consume_budget(config: HirakuConfig) -> None:
    harness = Harness(config)
    harness.start(
        "autoresearch",
        task="task",
        entrypoint="train",
        iterations=2,
        candidates=1,
        baseline_run="r0",
    )
    experiment(config, "e1", COMMIT_A)
    harness.reply("autoresearch_proposed", record_ids=["e1"])
    harness.reply("autoresearch_select", record_id="e1")
    harness.reply("autoresearch_implemented")
    harness.reply("autoresearch_decline", reason="Too expensive")
    assert harness.section() == "## Generate"
    assert harness.instruction["context"]["iteration"] == {"current": 2, "of": 2}

    experiment(config, "e2", COMMIT_A)
    harness.reply("autoresearch_proposed", record_ids=["e2"])
    harness.reply("autoresearch_select", record_id="e2")
    harness.reply("autoresearch_implemented")
    harness.reply("autoresearch_approve")
    harness.reply("autoresearch_report_failure", details="CUDA unavailable")
    assert harness.section() == "## Conclude"
    assert load_record("e2", config=config).to_dict()["assessment"]["status"] == (
        "launch_failed"
    )
    update_record("e2", changes={"conclusion": "Could not launch"}, config=config)
    done = harness.reply("autoresearch_concluded")
    assert [item["outcome"] for item in done["result"]["attempts"]] == [
        "declined",
        "failed",
    ]


def test_stop_completes_as_stopped(config: HirakuConfig) -> None:
    harness = Harness(config)
    config.set_active_record("base")
    harness.start("autoresearch", entrypoint="train", baseline_run="r0", candidates=1)
    experiment(config, "e1")
    harness.reply("autoresearch_proposed", record_ids=["e1"])
    harness.reply("autoresearch_select", record_id="e1")
    done = harness.reply("autoresearch_stop", reason="Out of time")
    assert done["result"]["outcome"] == "stopped"
    assert done["result"]["reason"] == "Out of time"
    assert done["result"]["attempts"][0]["outcome"] == "stopped"
    assert done["result"]["iterations"]["used"] == 0


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
