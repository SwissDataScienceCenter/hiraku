"""The approval gate — a decision, not a state (`docs/state.md`, section 2).

Approval is checked once, on a `proposed` node, before the scheduler will
move it into `implement` or `launch`. It does not appear in
`state.node_state` at all, and it is not re-checked separately before
`implement` and again before `launch` — clearing it once is enough for both.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .node import Node


def is_approved(node: Node) -> bool:
    """Whether `node` may be moved forward — implemented or launched.

    In `auto` mode this is unconditionally true: the decision that would
    otherwise need a person is exactly the thing `mode` is allowed to make
    optional (`docs/state.md`, section 2). In `guided` mode, nothing about
    the node's own evidence can substitute for a person's judgment about
    research direction, so this is true only once someone has actually
    approved it.
    """
    if node.mode == "auto":
        return True
    return node.approved_by is not None


def approve(node: Node, *, by: str) -> Node:
    """Record a `guided`-mode approval and persist it.

    A no-op, on purpose, if the node is already approved (re-approving does
    not change who approved it or when) or if it is in `auto` mode (the gate
    is already clear — see `is_approved` — and there is nothing to record).
    """
    if node.mode == "auto" or node.approved_by is not None:
        return node
    node.approved_by = by
    node.approved_at = datetime.now(UTC).isoformat()
    node.save()
    return node
