"""Tests for hiraku_auto's scheduler, ported onto hiraku's own Task/Experiment model."""

from __future__ import annotations

from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.data import Experiment, Task, load_experiment
from hiraku_auto.gates import approve, is_approved
from hiraku_auto.node import Node, create_node, list_nodes, load_node
from hiraku_auto.schedule import suggest_next
from hiraku_auto.state import node_state

COMMIT_A = "a" * 40


@pytest.fixture
def config(tmp_path: Path) -> HirakuConfig:
    return HirakuConfig.init(tmp_path)


@pytest.fixture
def task(config: HirakuConfig) -> Task:
    return Task.create(
        "vo-backend",
        config=config,
        goal="Improve validation accuracy",
        entrypoints={"train": {"command": ["python", "train.py"]}},
    )


# ---------------------------------------------------------------------------
# node.py — Node stays a fully ordinary Experiment as far as `hiraku` is
# concerned.
# ---------------------------------------------------------------------------


def test_a_node_is_a_plain_experiment_to_hiraku(
    config: HirakuConfig, task: Task
) -> None:
    create_node(
        "baseline",
        task=task,
        kind="baseline",
        origin="researcher",
        expectation="The unmodified upstream implementation establishes the reference.",
        config=config,
    )

    # hiraku's own loader, with no knowledge of Node, reads the same file fine.
    plain = load_experiment("baseline", task=task, config=config)
    assert isinstance(plain, Experiment)
    assert not isinstance(plain, Node)  # load_experiment builds a plain Experiment
    assert plain.is_proposal

    # Re-loading it as a Node recovers the extra fields hiraku doesn't know about.
    node = load_node("baseline", task=task, config=config)
    assert node.kind == "baseline"
    assert node.origin == "researcher"
    assert node.mode == "guided"  # the default


def test_list_nodes_matches_list_experiments_order(
    config: HirakuConfig, task: Task
) -> None:
    create_node("baseline", task=task, kind="baseline", config=config)
    create_node(
        "variant-a", task=task, kind="hypothesis", parent="baseline", config=config
    )

    nodes = list_nodes(task=task, config=config)
    assert [node.id for node in nodes] == ["baseline", "variant-a"]
    assert nodes[1].parent == "baseline"


def test_create_node_does_not_accept_a_commit_directly(
    config: HirakuConfig, task: Task
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
    config: HirakuConfig, task: Task
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
    config: HirakuConfig, task: Task
) -> None:
    node = create_node("baseline", task=task, mode="auto", config=config)
    assert is_approved(node)  # cleared without anyone approving

    approve(node, by="qianqian")  # a no-op — nothing to record
    assert node.approved_by is None


def test_reapproving_does_not_change_who_or_when(
    config: HirakuConfig, task: Task
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


def test_proposed_has_no_commit(config: HirakuConfig, task: Task) -> None:
    node = create_node("baseline", task=task, config=config)
    assert node_state(node, runs=[], has_children=False) == "proposed"


def test_implemented_has_a_commit_and_no_runs(config: HirakuConfig, task: Task) -> None:
    node = create_node("baseline", task=task, config=config)
    node.commit = COMMIT_A
    node.save()
    assert node_state(node, runs=[], has_children=False) == "implemented"


def test_awaiting_analysis_has_runs_and_no_verdict(
    config: HirakuConfig, task: Task
) -> None:
    node = create_node("baseline", task=task, config=config)
    node.commit = COMMIT_A
    node.save()
    assert node_state(node, runs=[object()], has_children=False) == "awaiting_analysis"


def test_supports_settles_even_without_children(
    config: HirakuConfig, task: Task
) -> None:
    node = create_node("baseline", task=task, config=config)
    node.commit = COMMIT_A
    node.verdict = "supports"
    node.save()
    assert node_state(node, runs=[object()], has_children=False) == "concluded_settled"


@pytest.mark.parametrize("verdict", ["refutes", "inconclusive"])
def test_refutes_or_inconclusive_without_children_is_untriaged(
    config: HirakuConfig, task: Task, verdict: str
) -> None:
    node = create_node("baseline", task=task, config=config)
    node.commit = COMMIT_A
    node.verdict = verdict
    node.save()
    state = node_state(node, runs=[object()], has_children=False)
    assert state == "concluded_untriaged"


def test_refutes_with_children_is_settled(config: HirakuConfig, task: Task) -> None:
    node = create_node("baseline", task=task, config=config)
    node.commit = COMMIT_A
    node.verdict = "refutes"
    node.save()
    assert node_state(node, runs=[object()], has_children=True) == "concluded_settled"


def test_abandoned_wins_over_everything_else(config: HirakuConfig, task: Task) -> None:
    node = create_node("baseline", task=task, config=config)
    node.commit = COMMIT_A
    node.verdict = "supports"
    node.abandoned = True
    node.save()
    assert node_state(node, runs=[object()], has_children=False) == "abandoned"


# ---------------------------------------------------------------------------
# schedule.py — suggest_next's priority order.
# ---------------------------------------------------------------------------


def test_empty_task_asks_to_design_or_discover(
    config: HirakuConfig, task: Task
) -> None:
    suggestion = suggest_next(task, config=config)
    assert suggestion["action"] == "design"
    assert "hiraku-auto design" in suggestion["next_commands"]
    assert "hiraku-auto discover" in suggestion["next_commands"]


def test_approved_proposed_node_asks_to_implement(
    config: HirakuConfig, task: Task
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
    config: HirakuConfig, task: Task
) -> None:
    create_node("baseline", task=task, config=config)
    suggestion = suggest_next(task, config=config)
    assert suggestion["action"] == "approve"
    assert suggestion["node"] == "baseline"


def test_auto_mode_skips_approve_and_goes_straight_to_implement(
    config: HirakuConfig, task: Task
) -> None:
    create_node("baseline", task=task, mode="auto", config=config)
    suggestion = suggest_next(task, config=config)
    assert suggestion["action"] == "implement"


def test_implemented_approved_node_asks_to_launch(
    config: HirakuConfig, task: Task
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


def test_implement_outranks_launch(config: HirakuConfig, task: Task) -> None:
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


def test_implement_and_launch_outrank_approve(config: HirakuConfig, task: Task) -> None:
    create_node("baseline", task=task, config=config)  # unapproved — asks to approve

    needs_code = create_node("variant-a", task=task, parent="baseline", config=config)
    approve(needs_code, by="qianqian")

    suggestion = suggest_next(task, config=config)
    assert suggestion["action"] == "implement"
    assert suggestion["node"] == "variant-a"


def test_node_with_results_and_no_verdict_asks_to_analyze(
    config: HirakuConfig, task: Task, monkeypatch: pytest.MonkeyPatch
) -> None:
    node = create_node("baseline", task=task, config=config)
    node.commit = COMMIT_A
    node.save()

    # No tracking backend is configured in this test project — stub `runs()`
    # rather than pull in a real backend just to produce one row of evidence
    # for the scheduler to react to.
    def fake_runs(self: Node, **_: object) -> list[object]:
        return [object()] if self.id == "baseline" else []

    monkeypatch.setattr(Node, "runs", fake_runs)

    suggestion = suggest_next(task, config=config)
    assert suggestion == {
        "action": "analyze",
        "node": "baseline",
        "why": "has results, no verdict yet",
    }


def test_analyze_outranks_triage_and_approve(config: HirakuConfig, task: Task) -> None:
    untriaged = create_node("dead-end", task=task, config=config)
    untriaged.commit = COMMIT_A
    untriaged.verdict = "refutes"
    untriaged.save()

    create_node("fresh-idea", task=task, config=config)  # would ask to approve

    suggestion = suggest_next(task, config=config)
    assert suggestion["action"] == "triage"
    assert suggestion["node"] == "dead-end"
    assert "refutes" in suggestion["why"]
