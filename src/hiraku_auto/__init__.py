"""hiraku_auto: the research-loop scheduler, built on `hiraku`'s own data model.

This package does not reimplement records, tasks, experiments, or launching —
`hiraku` already owns all of that, and every file this package writes is a
completely ordinary `hiraku` experiment record. `hiraku experiment show`,
`hiraku run list`, and `hiraku launch` all work unmodified on a node this
package creates.

What this package adds is the scheduling layer described in `docs/state.md`:
a handful of extra front-matter fields on top of `Experiment` (`Node`, in
`node.py`), a way to read a node's position in that lifecycle from its own
evidence (`state.py`), an approval gate that is optional depending on `mode`
rather than a stored status (`gates.py`), and the priority policy that picks
one thing to do next across a whole task's worth of nodes (`schedule.py`).
`hiraku.records.Record` already allows extra fields on every record
(`model_config = ConfigDict(extra="allow")`), which is what makes all of this
possible without changing anything in `hiraku` itself.

Scope of this first version: the deterministic scheduling skeleton — state
derivation, the approval gate, and `suggest_next()`. It does not yet include
the LLM-driven content generation for `design`, `discover`, `triage`, or
`analyze` (deciding *what* a new node should say, or *what a result means*)
— `schedule.py`'s docstring says exactly where that plugs in next.
"""

from .gates import approve, is_approved
from .node import (
    DECISIONS,
    KINDS,
    MODES,
    ORIGINS,
    VERDICTS,
    Node,
    create_node,
    list_nodes,
    load_node,
)
from .state import STATES, node_state

__all__ = [
    "DECISIONS",
    "KINDS",
    "MODES",
    "ORIGINS",
    "VERDICTS",
    "Node",
    "STATES",
    "approve",
    "create_node",
    "is_approved",
    "list_nodes",
    "load_node",
    "node_state",
]
