"""`suggest_next()`: the goal-level scheduling policy (`docs/state.md`, section 5).

Ported onto `hiraku`'s own model: what was `suggest_next(forest, goal_id)`
over a from-scratch record tree is now `suggest_next(task_id)`, reading
`Node` records that live under the exact root record directory `hiraku`
itself manages. `hiraku record show`/`list` see the same files; they just
don't know the shape of the extra fields (`Record`'s `extra="allow"` is what
makes that safe).

**Scope of this module.** It answers "what's the single next thing to do,"
never *how* to do it. `approve`/`launch` are real, mechanical actions:
`gates.approve` records approval, and `hiraku launch <id>:<entrypoint>` is
what "launch" resolves to — this module only ever says *that* one of these
is next, it never calls the agent or runs the command itself.
`design`/`discover`/`implement`/`triage`/`analyze` need judgement —
proposing what a new node should say, writing the code an approved proposal
describes, or deciding what a result means — and that judgement is an LLM
call, in `workflows.py` (`run_design`/`run_discover`/`run_implement`/
`run_triage`/`run_analyze`, via `engine.py`). `implement` still ends the
same way it always did, `hiraku record update <id> --commit <hash>` (or
its `run_implement` equivalent — see that function's own docstring); the
only thing that changed is who can be asked to get there.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from hiraku.config import HirakuConfig
from hiraku.record import Record

from .gates import is_approved
from .node import list_nodes
from .state import node_state


def suggest_next(
    task: Record | str, *, config: HirakuConfig | None = None
) -> dict[str, Any]:
    """The single next action for `task`, and why.

    Returns a dict with at least `action` and `why`; `design`/`exhausted`
    have no single `node` to name, everything else includes `node`. See the
    module docstring for what each action means and does not yet mean.
    """
    selected = config or HirakuConfig.discover()
    task_id = task.id if isinstance(task, Record) else str(task)
    nodes = list_nodes(task=task, config=selected)

    if not nodes:
        return {
            "action": "design",
            "why": "the task has no experiments yet",
            "next_commands": [
                f"hiraku-agent design --task {task_id}",
                f"hiraku-agent discover --task {task_id}",
            ],
        }

    children_of: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        if node.parent_id:
            children_of[node.parent_id].append(node.id)

    runs_by_id = {node.id: node.runs(config=selected) for node in nodes}
    states = {
        node.id: node_state(
            node, runs_by_id[node.id], has_children=bool(children_of[node.id])
        )
        for node in nodes
    }
    by_id = {node.id: node for node in nodes}

    # Priority 1 — approved and proposed: no code yet.
    for node_id, state in states.items():
        if state == "proposed" and is_approved(by_id[node_id]):
            return {
                "action": "implement",
                "node": node_id,
                "why": "approved, but no code bound to it yet",
            }

    # Priority 1 — approved and implemented: never run.
    for node_id, state in states.items():
        if state == "implemented" and is_approved(by_id[node_id]):
            return {
                "action": "launch",
                "node": node_id,
                "why": "implemented and approved, never launched",
            }

    # Priority 2 — has results, no verdict yet.
    for node_id, state in states.items():
        if state == "awaiting_analysis":
            return {
                "action": "analyze",
                "node": node_id,
                "why": "has results, no verdict yet",
                "next_commands": [f"hiraku-agent analyze {node_id} --task {task_id}"],
            }

    # Priority 3 — concluded, no decision yet about whether to go further.
    # (Applies to a `supports` verdict too, not only `refutes`/`inconclusive` — a
    # positive result can still be worth a follow-up; see `state.node_state`.)
    for node_id, state in states.items():
        if state == "concluded":
            verdict = by_id[node_id].verdict
            return {
                "action": "triage",
                "node": node_id,
                "why": f"verdict is {verdict!r}, decide whether to go further",
                "next_commands": [f"hiraku-agent triage {node_id} --task {task_id}"],
            }

    # Priority 4 — proposed, gate not yet cleared. In `auto` mode
    # `is_approved` is already true above, so a node only reaches here in
    # `guided` mode.
    for node_id, state in states.items():
        if state == "proposed":
            return {
                "action": "approve",
                "node": node_id,
                "why": "awaiting the approval gate",
                "next_commands": [
                    f"hiraku-agent approve {node_id} --task {task_id} --by <you>"
                ],
            }

    return {"action": "exhausted", "why": "nothing left to schedule"}
