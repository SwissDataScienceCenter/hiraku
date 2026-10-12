"""Deriving a node's lifecycle state from its own evidence (`docs/state.md`, section 1).

Every state below is a fact about what exists for the node — not a decision
about it. Approval is handled separately in `gates.py`: it is a decision,
not a fact, and does not appear here.

**`running`** is the one state that is not a fact about the node's own record: it
comes from `launches.running_nodes()` — a run attributed to the node that says it is
still running (read from the tracking backend), or a marker set with `hiraku-agent
mark-running` — so an experiment that runs for hours stays visible between two separate
calls. The caller passes it in (`running=`) the way it passes `runs`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from hiraku.config import HirakuConfig

from .launches import running_nodes
from .node import Node, cached_runs

STATES = (
    "proposed",
    "implemented",
    "running",
    "awaiting_analysis",
    "concluded",
    "completed",
)


def node_state(
    node: Node, runs: Sequence[Any], *, has_children: bool, running: bool = False
) -> str:
    """`node`'s position on the lifecycle path.

    Args:
        node: The node to place.
        runs: This node's own runs (e.g. `node.runs(config=...)`), already
            fetched by the caller — passed in rather than fetched here so a
            caller scoring a whole task's nodes can fetch runs once per node
            instead of once per `node_state` call.
        has_children: Whether any other node in the task has
            `parent_id == node.id`. A node cannot answer this from its own
            record alone, since "child" is a fact about a *different*
            record's `parent_id` field — the caller must compute it across
            the whole task's nodes.
        running: Whether a launch of this node is in flight (see the module docstring).

    Returns:
        One of `STATES`.
    """
    if node.abandoned:
        return "completed"
    if node.is_proposal:
        # `commit is None` — no real code exists yet, whatever else might be
        # set on the record.
        return "proposed"
    if running:
        # In flight: whatever runs exist so far are partial, so neither a new analysis
        # nor a triage of an older verdict should be suggested until it ends.
        return "running"
    if node.verdict is not None:
        # A recorded verdict is the authoritative sign that analysis
        # happened, independent of whatever `runs` a tracking-backend lookup
        # happens to return right now (a stale or misconfigured backend must
        # not make an already-judged node look unjudged again).
        #
        # Unlike the old model, a `supports` verdict does NOT settle this by
        # itself: a positive result can still be worth going deeper on (a
        # robustness check, a natural ablation, pushing the same idea
        # further) exactly as a negative one can — so both go through the
        # same `concluded` → `triage` decision. What ends it is either
        # `triage` having actually produced children (`has_children`), or
        # `triage` having completed and explicitly said "no further
        # children" (`node.triaged`) — `run_analyze` resets `triaged` to
        # `False` on every *new* verdict, so a fresh verdict always gets a
        # fresh decision.
        if has_children or node.triaged:
            return "completed"
        return "concluded"
    if runs:
        return "awaiting_analysis"
    return "implemented"


def node_bookkeeping(
    nodes: list[Node], *, config: HirakuConfig
) -> tuple[dict[str, list[Any]], dict[str, str], dict[str, list[str]]]:
    """Each node's runs, derived state and children — what the scheduler and every
    workflow need before deciding anything about a tree."""
    runs_by_id, states, children_of, _ = tree_facts(nodes, config=config)
    return runs_by_id, states, children_of


def tree_facts(
    nodes: list[Node], *, config: HirakuConfig
) -> tuple[dict[str, list[Any]], dict[str, str], dict[str, list[str]], dict[str, Any]]:
    """`node_bookkeeping`, plus the in-flight markers behind each `running` state.

    Runs are read inside one `cached_runs()` block, so the tracking source is queried
    once per source rather than once per node.
    """
    children_of: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        if node.parent_id:
            children_of[node.parent_id].append(node.id)
    with cached_runs():
        runs_by_id = {node.id: node.runs(config=config) for node in nodes}
    in_flight = running_nodes(runs_by_id, config)
    states = {
        node.id: node_state(
            node,
            runs_by_id[node.id],
            has_children=bool(children_of[node.id]),
            running=node.id in in_flight,
        )
        for node in nodes
    }
    return runs_by_id, states, children_of, in_flight
