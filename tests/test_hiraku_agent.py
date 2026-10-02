"""Tests for hiraku_agent's scheduler, ported onto hiraku's unified Record model."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.record import Record, load_record
from hiraku_agent.gates import approve, is_approved
from hiraku_agent.node import Node, create_node, list_nodes, load_node
from hiraku_agent.schedule import suggest_next
from hiraku_agent.state import node_state

COMMIT_A = "a" * 40


@pytest.fixture
def config(tmp_path: Path) -> HirakuConfig:
    return HirakuConfig.init(tmp_path)


@pytest.fixture
def task(config: HirakuConfig) -> Record:
    return Record.create(
        "vo-backend",
        parent_id=None,
        type="task",
        config=config,
        goal="Improve validation accuracy",
        entrypoints={"train": {"command": ["python", "train.py"]}},
    )


# ---------------------------------------------------------------------------
# node.py — Node stays a fully ordinary Record as far as `hiraku` is
# concerned.
# ---------------------------------------------------------------------------


def test_a_node_is_a_plain_record_to_hiraku(config: HirakuConfig, task: Record) -> None:
    create_node(
        "baseline",
        task=task,
        kind="baseline",
        origin="researcher",
        expectation="The unmodified upstream implementation establishes the reference.",
        config=config,
    )

    # hiraku's own loader, with no knowledge of Node, reads the same file fine.
    plain = load_record("baseline", config=config)
    assert isinstance(plain, Record)
    assert not isinstance(plain, Node)  # load_record builds a plain Record
    assert plain.commit is None  # `is_proposal` itself is a Node-only convenience

    # Re-loading it as a Node recovers the extra fields hiraku doesn't know about.
    node = load_node("baseline", task=task, config=config)
    assert node.kind == "baseline"
    assert node.origin == "researcher"
    assert node.mode == "guided"  # the default


def test_list_nodes_matches_descendants_order(
    config: HirakuConfig, task: Record
) -> None:
    create_node("baseline", task=task, kind="baseline", config=config)
    create_node(
        "variant-a", task=task, kind="hypothesis", parent="baseline", config=config
    )

    nodes = list_nodes(task=task, config=config)
    assert [node.id for node in nodes] == ["baseline", "variant-a"]
    assert nodes[1].parent_id == "baseline"


def test_create_node_does_not_accept_a_commit_directly(
    config: HirakuConfig, task: Record
) -> None:
    # A node starts proposed; binding a commit is a separate, later step
    # (see node_state below).
    node = create_node("baseline", task=task, config=config)
    assert node.is_proposal
    assert node.commit is None


# ---------------------------------------------------------------------------
# gates.py — approval is optional by mode, not a stored status.
# ---------------------------------------------------------------------------


def test_guided_mode_needs_an_explicit_approval(
    config: HirakuConfig, task: Record
) -> None:
    node = create_node("baseline", task=task, mode="guided", config=config)
    assert not is_approved(node)

    approve(node, by="qianqian")
    assert is_approved(node)
    assert node.approved_by == "qianqian"
    assert node.approved_at is not None

    # Persisted, not just in memory.
    reloaded = load_node("baseline", task=task, config=config)
    assert reloaded.approved_by == "qianqian"


def test_auto_mode_clears_itself_and_records_nothing(
    config: HirakuConfig, task: Record
) -> None:
    node = create_node("baseline", task=task, mode="auto", config=config)
    assert is_approved(node)  # cleared without anyone approving

    approve(node, by="qianqian")  # a no-op — nothing to record
    assert node.approved_by is None


def test_reapproving_does_not_change_who_or_when(
    config: HirakuConfig, task: Record
) -> None:
    node = create_node("baseline", task=task, config=config)
    approve(node, by="qianqian")
    first_at = node.approved_at

    approve(node, by="johannes")
    assert node.approved_by == "qianqian"
    assert node.approved_at == first_at


# ---------------------------------------------------------------------------
# state.py — every state is derived from evidence, not stored.
# ---------------------------------------------------------------------------


def test_proposed_has_no_commit(config: HirakuConfig, task: Record) -> None:
    node = create_node("baseline", task=task, config=config)
    assert node_state(node, runs=[], has_children=False) == "proposed"


def test_implemented_has_a_commit_and_no_runs(
    config: HirakuConfig, task: Record
) -> None:
    node = create_node("baseline", task=task, config=config)
    node.commit = COMMIT_A
    node.save()
    assert node_state(node, runs=[], has_children=False) == "implemented"


def test_awaiting_analysis_has_runs_and_no_verdict(
    config: HirakuConfig, task: Record
) -> None:
    node = create_node("baseline", task=task, config=config)
    node.commit = COMMIT_A
    node.save()
    assert node_state(node, runs=[object()], has_children=False) == "awaiting_analysis"


@pytest.mark.parametrize("verdict", ["supports", "refutes", "inconclusive"])
def test_any_verdict_without_a_decision_is_concluded(
    config: HirakuConfig, task: Record, verdict: str
) -> None:
    """A `supports` verdict no longer settles by itself — see `state.node_state`'s
    docstring: a positive result can still be worth a follow-up, exactly like a
    negative one, so every fresh verdict lands on `concluded` until something
    (`triage`) actually decides whether to go further."""
    node = create_node("baseline", task=task, config=config)
    node.commit = COMMIT_A
    node.verdict = verdict
    node.save()
    assert node_state(node, runs=[object()], has_children=False) == "concluded"


@pytest.mark.parametrize("verdict", ["supports", "refutes", "inconclusive"])
def test_any_verdict_with_children_is_completed(
    config: HirakuConfig, task: Record, verdict: str
) -> None:
    node = create_node("baseline", task=task, config=config)
    node.commit = COMMIT_A
    node.verdict = verdict
    node.save()
    assert node_state(node, runs=[object()], has_children=True) == "completed"


@pytest.mark.parametrize("verdict", ["supports", "refutes", "inconclusive"])
def test_any_verdict_already_triaged_with_no_children_is_completed(
    config: HirakuConfig, task: Record, verdict: str
) -> None:
    """`triage` deciding "no, don't go further" produces no children — `triaged` is
    the only record that a decision was actually made, so it alone (besides having
    children) must be enough to reach `completed`."""
    node = create_node("baseline", task=task, config=config)
    node.commit = COMMIT_A
    node.verdict = verdict
    node.triaged = True
    node.save()
    assert node_state(node, runs=[object()], has_children=False) == "completed"


def test_abandoned_wins_over_everything_else(
    config: HirakuConfig, task: Record
) -> None:
    node = create_node("baseline", task=task, config=config)
    node.commit = COMMIT_A
    node.verdict = "supports"
    node.abandoned = True
    node.save()
    assert node_state(node, runs=[object()], has_children=False) == "completed"


# ---------------------------------------------------------------------------
# schedule.py — suggest_next's priority order.
# ---------------------------------------------------------------------------


def test_empty_task_asks_to_design_or_discover(
    config: HirakuConfig, task: Record
) -> None:
    suggestion = suggest_next(task, config=config)
    assert suggestion["action"] == "design"
    assert any(
        cmd.startswith("hiraku-agent design") for cmd in suggestion["next_commands"]
    )
    assert any(
        cmd.startswith("hiraku-agent discover") for cmd in suggestion["next_commands"]
    )


def test_approved_proposed_node_asks_to_implement(
    config: HirakuConfig, task: Record
) -> None:
    node = create_node("baseline", task=task, config=config)
    approve(node, by="qianqian")
    suggestion = suggest_next(task, config=config)
    assert suggestion == {
        "action": "implement",
        "node": "baseline",
        "why": "approved, but no code bound to it yet",
    }


def test_unapproved_proposed_node_asks_to_approve(
    config: HirakuConfig, task: Record
) -> None:
    create_node("baseline", task=task, config=config)
    suggestion = suggest_next(task, config=config)
    assert suggestion["action"] == "approve"
    assert suggestion["node"] == "baseline"


def test_auto_mode_skips_approve_and_goes_straight_to_implement(
    config: HirakuConfig, task: Record
) -> None:
    create_node("baseline", task=task, mode="auto", config=config)
    suggestion = suggest_next(task, config=config)
    assert suggestion["action"] == "implement"


def test_implemented_approved_node_asks_to_launch(
    config: HirakuConfig, task: Record
) -> None:
    node = create_node("baseline", task=task, config=config)
    approve(node, by="qianqian")
    node.commit = COMMIT_A
    node.save()
    suggestion = suggest_next(task, config=config)
    assert suggestion == {
        "action": "launch",
        "node": "baseline",
        "why": "implemented and approved, never launched",
    }


def test_implement_outranks_launch(config: HirakuConfig, task: Record) -> None:
    launchable = create_node("baseline", task=task, config=config)
    approve(launchable, by="qianqian")
    launchable.commit = COMMIT_A
    launchable.save()

    needs_code = create_node("variant-a", task=task, parent="baseline", config=config)
    approve(needs_code, by="qianqian")

    suggestion = suggest_next(task, config=config)
    assert suggestion == {
        "action": "implement",
        "node": "variant-a",
        "why": "approved, but no code bound to it yet",
    }


def test_implement_and_launch_outrank_approve(
    config: HirakuConfig, task: Record
) -> None:
    create_node("baseline", task=task, config=config)  # unapproved — asks to approve

    needs_code = create_node("variant-a", task=task, parent="baseline", config=config)
    approve(needs_code, by="qianqian")

    suggestion = suggest_next(task, config=config)
    assert suggestion["action"] == "implement"
    assert suggestion["node"] == "variant-a"


def test_node_with_results_and_no_verdict_asks_to_analyze(
    config: HirakuConfig, task: Record, monkeypatch: pytest.MonkeyPatch
) -> None:
    node = create_node("baseline", task=task, config=config)
    node.commit = COMMIT_A
    node.save()

    # HirakuConfig defaults to a real CSV backend reading <root>/runs.csv
    # (HirakuConfig.DEFAULTS["tracking_backend"]) — no runs.csv file exists
    # in this fresh tmp_path project, so the real backend legitimately
    # returns []. Stubbing `runs()` here is only to avoid writing a CSV file
    # by hand for a test about scheduling priority, not because no backend
    # is configured — see test_a_real_csv_backend_row_moves_the_scheduler
    # below for the same transition through the unmocked backend.
    def fake_runs(self: Node, **_: object) -> list[object]:
        return [object()] if self.id == "baseline" else []

    monkeypatch.setattr(Node, "runs", fake_runs)

    suggestion = suggest_next(task, config=config)
    assert suggestion == {
        "action": "analyze",
        "node": "baseline",
        "why": "has results, no verdict yet",
        "next_commands": ["hiraku-agent analyze baseline --task vo-backend"],
    }


def test_a_real_csv_backend_row_moves_the_scheduler(
    config: HirakuConfig, task: Record
) -> None:
    """No monkeypatching: a real runs.csv row, read by hiraku's own CSVBackend.

    Confirms `Node.runs()` (overridden in `node.py` to filter the inherited
    `Record.runs()` call back down to this node's own `record_id` — see that
    override's docstring for why the filter is needed at all now) actually reaches
    a real tracking backend — the same one `hiraku runs --record` itself uses — not
    just a shape this module's tests assume.
    """
    node = create_node("baseline", task=task, config=config)
    approve(node, by="qianqian")
    node.commit = COMMIT_A
    node.save()
    assert suggest_next(task, config=config)["action"] == "launch"

    runs_csv = config.root / "runs.csv"
    with runs_csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "id",
                "record_id",
                "commit",
                "timestamp",
                "entrypoint",
                "url",
                "metric.val_accuracy",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "id": "run-001",
                "record_id": "baseline",
                "commit": COMMIT_A,
                "timestamp": "2026-09-18T12:00:00Z",
                "entrypoint": "train",
                "url": "",
                "metric.val_accuracy": "0.87",
            }
        )

    runs = node.runs(config=config)
    assert len(runs) == 1
    assert runs[0].metrics["val_accuracy"] == "0.87"

    suggestion = suggest_next(task, config=config)
    assert suggestion == {
        "action": "analyze",
        "node": "baseline",
        "why": "has results, no verdict yet",
        "next_commands": ["hiraku-agent analyze baseline --task vo-backend"],
    }


def test_analyze_outranks_triage_and_approve(
    config: HirakuConfig, task: Record
) -> None:
    untriaged = create_node("dead-end", task=task, config=config)
    untriaged.commit = COMMIT_A
    untriaged.verdict = "refutes"
    untriaged.save()

    create_node("fresh-idea", task=task, config=config)  # would ask to approve

    suggestion = suggest_next(task, config=config)
    assert suggestion["action"] == "triage"
    assert suggestion["node"] == "dead-end"
    assert "refutes" in suggestion["why"]


def test_a_supports_verdict_still_asks_to_triage(
    config: HirakuConfig, task: Record
) -> None:
    """Unlike the old model, `supports` doesn't skip straight past triage — a
    positive result still needs a decision about whether it's worth going
    further on."""
    node = create_node("baseline", task=task, config=config)
    node.commit = COMMIT_A
    node.verdict = "supports"
    node.save()

    suggestion = suggest_next(task, config=config)
    assert suggestion["action"] == "triage"
    assert suggestion["node"] == "baseline"
    assert "go further" in suggestion["why"]


def test_triaged_node_with_no_children_is_exhausted_not_retriaged(
    config: HirakuConfig, task: Record
) -> None:
    node = create_node("baseline", task=task, config=config)
    node.commit = COMMIT_A
    node.verdict = "refutes"
    node.triaged = True
    node.save()

    suggestion = suggest_next(task, config=config)
    assert suggestion["action"] == "exhausted"
