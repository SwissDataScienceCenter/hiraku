"""hiraku_agent: the research-loop scheduler, built on `hiraku`'s own data model.

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

On top of the deterministic scheduling skeleton — state derivation
(`state.py`), the approval gate (`gates.py`), and `suggest_next()`
(`schedule.py`) — this package also has the LLM-driven half: `engine.py`
calls an agent CLI (`claude -p` by default) and gets one JSON object back,
`prompts/` holds what each workflow asks it, and `workflows.py`'s
`run_design`/`run_discover`/`run_triage`/`run_analyze` are what turn that
reply into `Node` records. `cli.py` exposes all four, plus `next` and
`approve`, as `hiraku-agent design|discover|triage|analyze|next|approve`.
"""

from .engine import AgentRun, Hooks
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
from .schedule import suggest_next
from .state import STATES, node_state
from .workflows import run_analyze, run_design, run_discover, run_triage, task_state

__all__ = [
    "DECISIONS",
    "KINDS",
    "MODES",
    "ORIGINS",
    "STATES",
    "VERDICTS",
    "AgentRun",
    "Hooks",
    "Node",
    "approve",
    "create_node",
    "is_approved",
    "list_nodes",
    "load_node",
    "node_state",
    "run_analyze",
    "run_design",
    "run_discover",
    "run_triage",
    "suggest_next",
    "task_state",
]
