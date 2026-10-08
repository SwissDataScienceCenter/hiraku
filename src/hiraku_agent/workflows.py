"""draft/design/discover/implement/triage/analyze/update: the LLM-driven half of the
research loop.

`schedule.suggest_next` says *that* one of these is next; this module is what actually
runs one — building the state an agent sees, calling it through `engine.invoke`, and
writing what it returns as ordinary `hiraku_agent.Node` records. See `engine`'s module
docstring for the call mechanics, and `prompts/_rules.md` for the contract every
workflow shares — except `implement`, which is the one deliberate exception to "you do
not write files": see `run_implement`'s own docstring and `prompts/implement.md`.

The implementations live one per module in `hiraku_agent.flows`; they share one call
pipeline (`flows._common.AgentCall`: Action overrides, `--repo`, context, prompt,
invoke, parse) and one reply check per workflow (`hiraku_agent.replies`). This module is
the stable import surface: everything callers used to import from here still is.

Two decisions carried over from the pre-`hiraku` `hiraku_auto`, deliberately adapted
rather than copied:

- No separate "proposal" record type. The old version wrote `discover`'s output to a
  lighter-weight `research/proposals/` record, distinct from an "experiment". `hiraku`
  has exactly one kind of record for an attempt: `Record` (`Node`, here) — a
  proposal already exists as a `Node` with no commit (`Node.is_proposal`).
  `discover`'s accepted proposals become ordinary proposed nodes, exactly like
  `design`'s; there is no second file format to keep in sync.
- "Declined" ideas are recorded the same way. The old version wrote a `declined`
  proposal as its own file so a future `discover` wouldn't re-propose it. Here that's
  a `Node` with `abandoned=True` and no commit — `abandoned` already means exactly
  "considered, not pursued" (it's one of the two facts that resolve to `docs/state.md`'s
  `completed` state, the other being a node `triage` has already decided not to take
  further), so an idea nobody built is the same fact as a node somebody dropped; both
  are the novelty-check memory the six-condition test in `prompts/_rules.md` asks for.

Every node these workflows write is a plain `hiraku` record underneath — `hiraku record
show` reads it unmodified; only the extra fields carry meaning `hiraku` itself doesn't
know about (see `node.py`'s module docstring).
"""

from __future__ import annotations

from .flows._common import (
    STATE_BUDGET_CHARS,
    AgentCall,
    ensure_ids_free,
    task_state,
)
from .flows.analyze import PARTIAL_NOTE, run_analyze
from .flows.design import OPEN_ITEMS_HEADING, run_design
from .flows.discover import INJECTION_HEADING, INVESTIGATION_HEADING, run_discover
from .flows.draft import (
    CANDIDATES_SPECIFIED,
    CANDIDATES_UNSPECIFIED,
    DECISION_RULE_HEADING,
    DEFAULT_DECISION_RULE,
    PROTOCOL_HEADING,
    draft_state,
    run_draft,
    run_plan,
)
from .flows.implement import run_implement
from .flows.triage import run_triage
from .flows.update import (
    NODE_FIELDS,
    SUPERSEDED_HEADING,
    TASK_FIELDS,
    check_editable,
    is_task,
    run_revise,
    run_update,
)
from .state import node_bookkeeping

# Kept under its old name for callers that imported it from here.
_node_bookkeeping = node_bookkeeping

__all__ = [
    "CANDIDATES_SPECIFIED",
    "CANDIDATES_UNSPECIFIED",
    "DECISION_RULE_HEADING",
    "DEFAULT_DECISION_RULE",
    "INJECTION_HEADING",
    "INVESTIGATION_HEADING",
    "NODE_FIELDS",
    "OPEN_ITEMS_HEADING",
    "PARTIAL_NOTE",
    "PROTOCOL_HEADING",
    "STATE_BUDGET_CHARS",
    "SUPERSEDED_HEADING",
    "TASK_FIELDS",
    "AgentCall",
    "check_editable",
    "draft_state",
    "ensure_ids_free",
    "is_task",
    "node_bookkeeping",
    "run_analyze",
    "run_design",
    "run_discover",
    "run_draft",
    "run_implement",
    "run_plan",
    "run_revise",
    "run_triage",
    "run_update",
    "task_state",
]
