"""hiraku_agent: the research-loop scheduler, built on `hiraku`'s own data model.

This package does not reimplement records, tasks, or launching — `hiraku`
already owns all of that, and every file this package writes is a completely
ordinary `hiraku` record. `hiraku record show`, `hiraku runs`, and `hiraku
launch` all work unmodified on a node this package creates. A "task" here is
just a root record (`parent_id is None`); `hiraku.record` itself draws no
class distinction between a task and a node under it — see `node.py`'s
module docstring.

What this package adds is the scheduling layer described in `docs/state.md`:
a handful of extra front-matter fields on top of `Record` (`Node`, in
`node.py`), a way to read a node's position in that lifecycle from its own
evidence (`state.py`), an approval gate that is optional depending on `mode`
rather than a stored status (`gates.py`), and the priority policy that picks
one thing to do next across a whole task's worth of nodes (`schedule.py`).
`hiraku.base.BaseRecord` already allows extra fields on every record
(`model_config = ConfigDict(extra="allow")`), which is what makes all of this
possible without changing anything in `hiraku` itself.

On top of the deterministic scheduling skeleton — state derivation
(`state.py`), the approval gate (`gates.py`), and `suggest_next()`
(`schedule.py`) — this package also has the LLM-driven half: `engine.py`
calls an agent CLI (`claude -p` by default) and gets one JSON object back,
`prompts/` holds what each workflow asks it, and `workflows.py`'s
`run_draft`/`run_design`/`run_discover`/`run_triage`/`run_analyze` are what
turn that reply into a task (a root `Record`) or `Node` record. `cli.py`
exposes all five, plus `next` and `approve`, as
`hiraku-agent draft|design|discover|triage|analyze|next|approve`.

Two more modules bridge these workflows to `hiraku.agent`'s `Action`/
`Context`/`Program` records — plain data, never code (see `hiraku.agent`'s
own docstring: "execution is out of scope"). `actions.py` translates a saved
`Action`/`Context` record's fields into the keyword arguments the workflow
functions already take (`--action`/`--context-id` on any of the five
commands); `loop.py`'s `run_loop` repeats that translation across a whole
bounded loop driven by a `Program` record (`hiraku-agent loop`). Neither
module changes what the workflow functions do or take away their existing,
record-free call path.
"""

from .engine import AgentRun, Hooks
from .gates import approve, is_approved
from .loop import LoopResult, LoopStep, run_loop
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
from .workflows import (
    run_analyze,
    run_design,
    run_discover,
    run_draft,
    run_triage,
    task_state,
)

__all__ = [
    "DECISIONS",
    "KINDS",
    "MODES",
    "ORIGINS",
    "STATES",
    "VERDICTS",
    "AgentRun",
    "Hooks",
    "LoopResult",
    "LoopStep",
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
    "run_draft",
    "run_loop",
    "run_triage",
    "suggest_next",
    "task_state",
]
