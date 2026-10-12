"""`Node`: the extra fields the scheduler needs, layered on `hiraku.record.Record`.

`Record` already carries everything a bare commit-bound attempt needs: `parent_id`,
`commit`, `branch`, `entrypoints`, `metrics`. Every `hiraku.base.BaseRecord` sets
`model_config = ConfigDict(extra="allow")`, so a richer workflow can add its own
front-matter fields without any change to `hiraku`'s own schema — `Node` just names
the fields this scheduler depends on and gives them real types instead of leaving
them as untyped extras.

Nothing here changes how `hiraku` itself reads a `Node`'s file. `hiraku record show`
still reads it as an ordinary record; these fields just come back as extras it
doesn't know the shape of. As far as `hiraku` and its CLI are concerned, a `Node` is
a `Record`.

A node's place in the research tree is `Record.parent_id` itself now — arbitrary
depth, native to `hiraku` core — rather than a field `hiraku_agent` had to maintain
by hand on top of a flat, two-level task/experiment pair (the earlier model gave
every experiment a required `task_id` naming its task, with `hiraku_agent`'s own
`parent` field layered on top for deeper nesting below that). The unified `Record`
model collapses both into one `parent_id`, so `Node` no longer needs a `parent`
field of its own. A node with no ancestors at all (`parent_id is None`) is a *task*:
the root of one research tree, created by `run_draft` (`hiraku record create ... --type
task` by hand) — see `task_of()` below for walking back up to it from any node.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Literal

from hiraku.config import HirakuConfig
from hiraku.errors import ValidationError
from hiraku.record import Record, load_record

KINDS = ("baseline", "hypothesis", "diagnostic", "ablation", "combination")
ORIGINS = ("researcher", "agent-diagnostic", "agent-literature")
MODES = ("guided", "auto")
VERDICTS = ("supports", "refutes", "inconclusive")
# A judgment about the hypothesis (verdict) and a decision about whether to
# keep this attempt's git history (decision) answer different questions —
# see docs/state.md's "gates" note and the open question this resolves.
# `decision` mirrors the vocabulary the autoresearch example already uses in
# `hiraku record update --set decision=...`.
DECISIONS = ("keep", "discard", "crash")

_Kind = Literal["baseline", "hypothesis", "diagnostic", "ablation", "combination"]
_Origin = Literal["researcher", "agent-diagnostic", "agent-literature"]
_Mode = Literal["guided", "auto"]
_Verdict = Literal["supports", "refutes", "inconclusive"]
_Decision = Literal["keep", "discard", "crash"]


class Node(Record):
    """A `Record` plus the extra fields `hiraku_agent`'s scheduler reads.

    Attributes:
        kind: What sort of node this is — see `KINDS`.
        origin: Who proposed it — see `ORIGINS`.
        mode: `guided` (the approval gate stops for a person) or `auto` (it
            clears itself). See `hiraku_agent.gates`.
        expectation: One falsifiable sentence: predicted direction, metric,
            mechanism.
        verdict: What the evidence showed about the hypothesis — see
            `VERDICTS`. Set once, by `analyze`, together with the run(s) it
            is based on.
        decision: Whether to keep this attempt's commit — see `DECISIONS`.
            Independent of `verdict`: a node can support its hypothesis and
            still be a `discard` (e.g. superseded by a cleaner rerun), or
            refute it and still be a `keep` (e.g. a documented dead end).
        approved_by: Who cleared the approval gate in `guided` mode. `None`
            in `auto` mode, where nothing needs to have signed anything
            (`hiraku_agent.gates.is_approved` checks `mode` first).
        approved_at: When `approved_by` was recorded, as an ISO-8601 string
            — `Record`'s YAML front matter only accepts
            str/int/float/bool/None/list/mapping, not a raw `datetime`.
        abandoned: Explicitly dropped. One of the two ways a node reaches
            `completed` with no evidence to derive it from — dropping a node
            is always a decision, so it always needs an explicit flag. See
            `state.node_state`.
        triaged: Whether `triage` has already recorded a go/no-go decision
            for this node's *current* verdict. The other way (besides
            `abandoned` and already having children) a `concluded` node
            becomes `completed`: a node can conclude with no children and
            still be done, once something has judged it and said so — a
            `supports` verdict that doesn't warrant a follow-up ablation is
            just as "decided" as a `refutes` one that doesn't. Set by
            `workflows.run_triage` when it completes (not when it reports
            `blocked`); reset to `False` by `workflows.run_analyze` whenever
            it writes a *new* verdict, since a fresh verdict needs a fresh
            decision — see `state.node_state`.
        analyzed_runs: The run ids the latest `analyze` call judged. When that call
            set no verdict and the node's runs are still exactly these, analysing
            again cannot change anything, so `schedule.suggest_next` asks for more
            evidence instead of repeating the call.
        execution_defect: Set by `triage` when it classifies the result as class A
            (the runs did not test the expectation). The verdict is cleared, and
            `schedule.suggest_next` asks for a fix rather than a new analysis until
            runs other than `defect_runs` exist.
        defect_runs: The run ids `triage` found defective. `analyze` is told not to
            use them as evidence.

    Note:
        `parent_id` (inherited from `Record`) is this node's place in the
        tree. Every node `hiraku_agent` itself creates has one, pointing
        either directly at its task or at another node — see `task_of()`.
    """

    kind: _Kind | None = None
    origin: _Origin | None = None
    mode: _Mode = "guided"
    expectation: str | None = None
    verdict: _Verdict | None = None
    decision: _Decision | None = None
    approved_by: str | None = None
    approved_at: str | None = None
    abandoned: bool = False
    triaged: bool = False
    analyzed_runs: list[str] | None = None
    execution_defect: bool = False
    defect_runs: list[str] | None = None

    @property
    def is_proposal(self) -> bool:
        """Whether this node has not yet been assigned a commit.

        `hiraku.record.Record` itself has no notion of a "proposal" — this was a
        property of the old two-class model's `Experiment`, ported here since a
        `Node` is exactly that same kind of thing underneath.
        """
        return self.commit is None

    def runs(
        self, *, backend: Any = None, config: Any = None, **query: Any
    ) -> list[Any]:
        """This node's own attributed runs.

        As of the unified-records model, `hiraku.record.Record.runs()` (see
        `hiraku/tracking/base.py`'s `list_all_recorded_runs`) no longer scopes to the
        record it is called on — it returns every run attributed to *any* record
        sharing the resolved tracking source (confirmed directly: a root's and its
        child's `.runs()` return identical rows once both have attributed CSV rows).
        `hiraku runs --record ID` embraces that — it means "everything this tracking
        source has recorded" now that `--descendants`/`--include-unattributed` are
        gone — but the scheduler needs the opposite: `state.node_state` reads "has
        runs" as "this node has a result", not "something in the task does". This
        override restores that per-node scoping on top of the inherited call.

        Filtering after the inherited call, rather than before, is only correct
        because nothing in this package passes `sort`/`limit`/`ascending` through
        `node.runs()` — those apply across *all* attributed runs before this would
        get a chance to narrow them down to `self.id`, so a future caller that needs
        them together should filter before ranking, not reuse this as-is.
        """
        cache = _RUN_CACHE.get()
        if cache is None or backend is not None or query:
            all_runs = super().runs(backend=backend, config=config, **query)
        else:
            key = _source_key(self, config)
            if key not in cache:
                cache[key] = super().runs(config=config)
            all_runs = cache[key]
        return [run for run in all_runs if getattr(run, "record_id", None) == self.id]


# One tracking-source query per source within a `cached_runs()` block, instead of one
# per `Node.runs()` call: building one prompt or one scheduling decision used to scan
# the whole source 2N+1 times for N nodes, which a remote MLflow/W&B server feels.
_RUN_CACHE: ContextVar[dict[str, list[Any]] | None] = ContextVar(
    "hiraku_agent_run_cache", default=None
)


@contextmanager
def cached_runs() -> Iterator[None]:
    """Query each tracking source at most once inside this block.

    Meant for one read-only pass — building the state for a prompt, or one
    `suggest_next` decision — not across an agent call: runs that land while an agent
    works must be seen by the write that follows it. Nested blocks share the outer
    cache.
    """
    if _RUN_CACHE.get() is not None:
        yield
        return
    token = _RUN_CACHE.set({})
    try:
        yield
    finally:
        _RUN_CACHE.reset(token)


def _source_key(record: Record, config: Any) -> str:
    """What decides which runs `Record.runs()` returns: the project, and the effective
    `tracking_backend` and `mlflow_experiment` (both inherited down the tree)."""
    selected = config or HirakuConfig.discover()
    values = record.resolve(config=selected).values
    return json.dumps(
        [
            str(selected.root),
            values.get("tracking_backend"),
            values.get("mlflow_experiment"),
        ],
        sort_keys=True,
        default=str,
    )


def task_of(node: Record, *, config: HirakuConfig | None = None) -> Record:
    """Walk `parent_id` up to the task (root record) `node` ultimately belongs to.

    Replaces the old two-class model's `Experiment.task_id` — a stored,
    authoritative pointer that never needed a lookup to read. The unified `Record`
    model only stores the immediate `parent_id`, so finding the root costs one
    `load_record` call per level of nesting — shallow in practice, since
    `hiraku_agent` trees (task -> node -> triage-produced child -> ...) are rarely
    more than a handful of levels deep.
    """
    selected = config or HirakuConfig.discover()
    current: Record = node
    while current.parent_id is not None:
        current = load_record(current.parent_id, config=selected)
    return current


def create_node(
    node_id: str,
    *,
    task: Record | str | None = None,
    parent: str | None = None,
    kind: str | None = None,
    origin: str | None = None,
    expectation: str | None = None,
    mode: str = "guided",
    markdown: str = "",
    config: HirakuConfig | None = None,
    **fields: Any,
) -> Node:
    """Create a new node — a thin call to `Record.create` (see its docstring).

    `parent`, when given, is this node's immediate parent — another node, for a
    `triage`-produced child. Otherwise the node is created directly under `task`
    (a task id, or the task's own loaded record). Exactly one of `task`/`parent`
    matters for placement; both simply resolve to one `Record.parent_id` now,
    where the old two-class model kept them as two separate fields
    (`Experiment.task_id` plus `hiraku_agent`'s own `parent`). At least one of
    `task`/`parent` is required — a `Node` with no parent at all is a task, and
    tasks are created by `run_draft`, not this function.

    Deliberately does not accept `commit`: a node is created `proposed` (see
    `state.node_state`), and moves to `implemented` only once real code exists to
    bind — by hand with `hiraku record update <id> --commit <hash>`, or by having
    an agent write that code and bind it itself with `hiraku-agent implement <id>`
    (`workflows.run_implement`; see its docstring for the approval gate that
    authorizes it to write anything at all).
    """
    if parent is not None:
        parent_id = parent
    elif task is not None:
        parent_id = task.id if isinstance(task, Record) else task
    else:
        raise ValidationError(
            f"create_node({node_id!r}, ...) needs a 'task' or 'parent' to place it "
            f"under — a node with neither would be a task, not a node"
        )
    return Node.create(
        node_id,
        parent_id=parent_id,
        markdown=markdown,
        activate=False,
        config=config,
        kind=kind,
        origin=origin,
        expectation=expectation,
        mode=mode,
        **fields,
    )


def load_node(
    reference: str,
    *,
    task: Record | str | None = None,
    config: HirakuConfig | None = None,
) -> Node:
    """Load a node by id and re-read it as a `Node` to pick up the extra fields.

    Record ids are globally unique project-wide now (unlike the old two-class
    model's qualified `"{task_id}:{experiment_id}"` references), so a bare id
    always resolves to exactly one record — no task-scoped lookup or active-task
    fallback is needed any more. `task`, when given, is kept only as a sanity
    check: if the loaded node does not actually belong to that task, this raises
    rather than silently acting on the wrong tree.
    """
    selected = config or HirakuConfig.discover()
    record = load_record(reference, config=selected)
    node = Node.load(record.path)
    if task is not None:
        expected = task.id if isinstance(task, Record) else task
        actual = task_of(node, config=selected).id
        if actual != expected:
            raise ValidationError(
                f"Node {reference!r} belongs to task {actual!r}, not {expected!r}"
            )
    return node


def list_nodes(
    *,
    task: Record | str,
    config: HirakuConfig | None = None,
) -> list[Node]:
    """List every node under `task` — see `load_node` for why each is re-read as a
    `Node`.

    Replaces the old `list_experiments(task=...)` with `Record.descendants()`,
    which `hiraku` core now provides natively over the whole `parent_id` tree —
    `hiraku_agent` no longer needs to filter a flat per-task listing by hand.
    """
    selected = config or HirakuConfig.discover()
    root = task if isinstance(task, Record) else load_record(task, config=selected)
    return [Node.load(record.path) for record in root.descendants(config=selected)]
