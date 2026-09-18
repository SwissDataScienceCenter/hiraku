"""`Node`: the extra fields the scheduler needs, layered on `hiraku.data.Experiment`.

`Experiment` already carries everything a bare commit-bound run needs:
`task_id`, `summary`, `hypothesis`, `commit`, `branch`, `entrypoints`,
`metrics`. Every `hiraku.records.Record` sets
`model_config = ConfigDict(extra="allow")`, so a richer workflow can add its
own front-matter fields without any change to `hiraku`'s own schema — `Node`
just names the fields this scheduler depends on and gives them real types
instead of leaving them as untyped extras.

Nothing here changes how `hiraku` itself reads a `Node`'s file.
`hiraku experiment show --json` still reads it as an ordinary experiment;
these fields just come back as extras it doesn't know the shape of. As far
as `hiraku` and its CLI are concerned, a `Node` is an `Experiment`.
"""

from __future__ import annotations

from typing import Any, Literal

from hiraku.config import HirakuConfig
from hiraku.data import Experiment, Task, list_experiments, load_experiment

KINDS = ("baseline", "hypothesis", "diagnostic", "ablation", "combination")
ORIGINS = ("researcher", "agent-diagnostic", "agent-literature")
MODES = ("guided", "auto")
VERDICTS = ("supports", "refutes", "inconclusive")
# A judgment about the hypothesis (verdict) and a decision about whether to
# keep this attempt's git history (decision) answer different questions —
# see docs/state.md's "gates" note and the open question this resolves.
# `decision` mirrors the vocabulary the autoresearch example already uses in
# `hiraku experiment update --set decision=...`.
DECISIONS = ("keep", "discard", "crash")

_Kind = Literal["baseline", "hypothesis", "diagnostic", "ablation", "combination"]
_Origin = Literal["researcher", "agent-diagnostic", "agent-literature"]
_Mode = Literal["guided", "auto"]
_Verdict = Literal["supports", "refutes", "inconclusive"]
_Decision = Literal["keep", "discard", "crash"]


class Node(Experiment):
    """An `Experiment` plus the extra fields `hiraku_auto`'s scheduler reads and writes.

    Attributes:
        parent: The id of this node's parent within the same task, or `None`
            for a root. Stored explicitly rather than derived from git
            ancestry (as `hiraku.git.parent_experiments` does for committed
            experiments), because a `proposed` node has no commit yet and so
            no ancestry at all — the tree needs to hold a shape before any
            code exists for it.
        kind: What sort of node this is — see `KINDS`.
        origin: Who proposed it — see `ORIGINS`.
        mode: `guided` (the approval gate stops for a person) or `auto` (it
            clears itself). See `hiraku_auto.gates`.
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
            (`hiraku_auto.gates.is_approved` checks `mode` first).
        approved_at: When `approved_by` was recorded, as an ISO-8601 string
            — `Record`'s YAML front matter only accepts
            str/int/float/bool/None/list/mapping (see
            `Record._validate_yaml_value`), not a raw `datetime`.
        abandoned: Explicitly dropped. The one state in `docs/state.md` with
            no evidence to derive it from — dropping a node is always a
            decision, so it always needs an explicit flag.
    """

    parent: str | None = None
    kind: _Kind | None = None
    origin: _Origin | None = None
    mode: _Mode = "guided"
    expectation: str | None = None
    verdict: _Verdict | None = None
    decision: _Decision | None = None
    approved_by: str | None = None
    approved_at: str | None = None
    abandoned: bool = False


def create_node(
    node_id: str,
    *,
    task: Task | str | None = None,
    parent: str | None = None,
    kind: str | None = None,
    origin: str | None = None,
    expectation: str | None = None,
    mode: str = "guided",
    markdown: str = "",
    config: HirakuConfig | None = None,
    **fields: Any,
) -> Node:
    """Create a new node — a thin call to `Experiment.create` (see its docstring).

    Deliberately does not accept `commit`: a node is created `proposed` (see
    `state.node_state`), and moves to `implemented` only once real code
    exists to bind, via `hiraku experiment update <id> --commit <hash>` (or
    the `Node` equivalent once `implement` is ported — see `schedule.py`'s
    module docstring).
    """
    return Node.create(
        node_id,
        task=task,
        markdown=markdown,
        activate=False,
        config=config,
        parent=parent,
        kind=kind,
        origin=origin,
        expectation=expectation,
        mode=mode,
        **fields,
    )


def load_node(
    reference: str,
    *,
    task: Task | str | None = None,
    config: HirakuConfig | None = None,
) -> Node:
    """Resolve a node the same way `hiraku.data.load_experiment` resolves an experiment.

    Reuses `load_experiment` for reference resolution (qualified `task:id`
    references, the active task fallback, ambiguity errors) rather than
    re-implementing it, then reloads the same path as a `Node` to pick up
    the extra fields.
    """
    experiment = load_experiment(reference, task=task, config=config)
    return Node.load(experiment.path)


def list_nodes(
    *,
    task: Task | str | None = None,
    config: HirakuConfig | None = None,
) -> list[Node]:
    """List a task's nodes — see `load_node` for why each is re-read as a `Node`."""
    experiments = list_experiments(task=task, config=config)
    return [Node.load(experiment.path) for experiment in experiments]
