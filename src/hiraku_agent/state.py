"""Deriving a node's lifecycle state from its own evidence (`docs/state.md`, section 1).

Every state below is a fact about what exists for the node — not a decision
about it. Approval is handled separately in `gates.py`: it is a decision,
not a fact, and does not appear here.

**Known gap — `running` is not derived here.** `hiraku.launch.launch()` runs
a dispatch script via a blocking `subprocess` call (see the autoresearch
example: `hiraku launch <id>:train > run.log 2>&1` in a plain shell) — by
the time a `hiraku launch` invocation returns, the attempt is already
finished, crashed, or timed out. There is no persisted "still running"
signal to read back between two separate calls into this module, the way
there would be for a background dispatcher. A node this module hasn't seen
mid-launch is either `implemented` (no runs yet) or already past it (has
runs) — never caught "running" from the outside. Scheduling several
launches at once, if that's wanted, is a concern for whatever calls
`schedule.suggest_next()` in a loop (running more than one `hiraku launch`
concurrently in its own process), not something a node's file can record.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .node import Node

STATES = (
    "proposed",
    "implemented",
    "awaiting_analysis",
    "concluded_untriaged",
    "concluded_settled",
    "abandoned",
)


def node_state(node: Node, runs: Sequence[Any], *, has_children: bool) -> str:
    """`node`'s position on the lifecycle path.

    Args:
        node: The node to place.
        runs: This node's own runs (e.g. `node.runs(config=...)`), already
            fetched by the caller — passed in rather than fetched here so a
            caller scoring a whole task's nodes can fetch runs once per node
            instead of once per `node_state` call.
        has_children: Whether any other node in the task has
            `parent == node.id`. A node cannot answer this from its own
            record alone, since "child" is a fact about a *different*
            record's `parent` field — the caller must compute it across the
            whole task's nodes.

    Returns:
        One of `STATES`.
    """
    if node.abandoned:
        return "abandoned"
    if node.is_proposal:
        # `commit is None` — no real code exists yet, whatever else might be
        # set on the record.
        return "proposed"
    if node.verdict is not None:
        # A recorded verdict is the authoritative sign that analysis
        # happened, independent of whatever `runs` a tracking-backend lookup
        # happens to return right now (a stale or misconfigured backend must
        # not make an already-judged node look unjudged again).
        if node.verdict == "supports" or has_children:
            return "concluded_settled"
        return "concluded_untriaged"
    if runs:
        return "awaiting_analysis"
    return "implemented"
