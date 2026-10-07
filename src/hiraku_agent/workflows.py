"""draft/design/discover/implement/triage/analyze/update: the LLM-driven half of the
research loop.

`schedule.suggest_next` says *that* one of these is next; this module is what actually
runs one — building the state an agent sees, calling it through `engine.invoke`, and
writing what it returns as ordinary `hiraku_agent.Node` records. See `engine`'s module
docstring for the call mechanics, and `prompts/_rules.md` for the contract every
workflow shares — except `implement`, which is the one deliberate exception to "you do
not write files": see `run_implement`'s own docstring and `prompts/implement.md`.

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

Every node this module writes is a plain `hiraku` record underneath — `hiraku record
show` reads it unmodified; only the extra fields carry meaning `hiraku` itself doesn't
know about (see `node.py`'s module docstring).
"""

from __future__ import annotations

import datetime as _dt
import subprocess
from collections import defaultdict
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from typing import Any

from hiraku.config import HirakuConfig
from hiraku.errors import HirakuError
from hiraku.git import GitRepository
from hiraku.record import Record, list_records, load_record
from hiraku.validation import validate_identifier

from . import compare, sections
from .actions import apply_overrides
from .engine import (
    DEFAULT_IMPLEMENT_AGENT_CMD,
    IMPLEMENT_AGENT_CMD_ENV_VAR,
    UNVERIFIED_FILES_NOTE,
    UNVERIFIED_NOTE,
    AgentRun,
    Bundle,
    Hooks,
    agent_command,
    attach_context,
    build_prompt,
    extract_json,
    files_readable,
    gather_context,
    invoke,
    web_allowed,
    web_was_denied,
    with_repos,
)
from .errors import AgentError, AgentTimeout
from .gates import is_approved
from .launches import running_nodes
from .node import (
    KINDS,
    ORIGINS,
    Node,
    create_node,
    list_nodes,
    load_node,
    task_of,
)
from .state import node_state


def _require(data: dict[str, Any], key: str, kind: type) -> Any:
    value = data.get(key)
    if not isinstance(value, kind):
        raise AgentError(f"Agent reply is missing a valid '{key}' ({kind.__name__}).")
    return value


def _origin(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text if text in ORIGINS else default


def _write_sections(
    node: Node, item: dict[str, Any], section_map: dict[str, str]
) -> None:
    """Fold the freeform prose keys in `item` into `node.markdown` as `## Heading`
    sections.

    `section_map` is `{json_key: heading}`; a key that's missing or empty is skipped
    — an absent section is preferable to an empty one nobody asked for.
    """
    markdown = node.markdown
    for key, heading in section_map.items():
        value = item.get(key)
        if value:
            markdown = sections.set_section(markdown, heading, str(value))
    node.markdown = markdown


def _serialize_run(run: Any) -> dict[str, Any]:
    """`run.model_dump()`, tolerant of `hiraku.run.Run`'s normalized fields.

    `Run.metrics`/`.params` are frozen into `types.MappingProxyType` (see `Run`'s own
    docstring: "normalized to immutable mapping proxies"), a type pydantic's JSON mode
    doesn't know how to serialize on its own — it raises `PydanticSerializationError`
    rather than silently guessing. `mode="python"` sidesteps that, and this then plainly
    converts anything mapping-shaped to a `dict` and anything with `.isoformat()` (the
    `timestamp` field) to a string, so the result is still safe to hand to `json.dumps`
    when `engine.build_prompt` embeds it in a prompt.
    """
    data = run.model_dump(mode="python", warnings=False)
    for key, value in list(data.items()):
        if isinstance(value, Mapping) and not isinstance(value, dict):
            data[key] = dict(value)
        elif hasattr(value, "isoformat"):
            data[key] = value.isoformat()
    return data


def _node_bookkeeping(
    nodes: list[Node], *, config: HirakuConfig
) -> tuple[dict[str, list], dict[str, str], dict[str, list[str]]]:
    """Shared bookkeeping every workflow needs: each node's runs, derived state, and
    children."""
    children_of: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        if node.parent_id:
            children_of[node.parent_id].append(node.id)
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
    return runs_by_id, states, children_of


def task_state(
    task: Record,
    nodes: list[Node],
    *,
    config: HirakuConfig,
    focus: str | None = None,
) -> dict[str, Any]:
    """The state an agent sees: the task's own record, plus every node scoped to it.

    Scoped to one task on purpose, the way the old `hiraku_auto`'s `slim_state` scoped
    to one goal — it keeps the prompt small and stops the agent reasoning across
    incomparable protocols. `hiraku_agent` has no separate goal layer to scope by (the
    task's own `Record` already carries `goal`/`question`/`metrics` as extras — see
    `node.py`'s module docstring and `docs/state.md`), so scoping by task is the whole
    of it.
    """
    runs_by_id, states, children_of = _node_bookkeeping(nodes, config=config)
    serialized: dict[str, Any] = {}
    for node in nodes:
        payload = node.model_dump(mode="json", exclude={"path"})
        payload["runs"] = [_serialize_run(run) for run in runs_by_id[node.id]]
        payload["is_proposal"] = node.is_proposal
        payload["approved"] = is_approved(node)
        payload["state"] = states[node.id]
        payload["children"] = list(children_of.get(node.id, []))
        serialized[node.id] = payload

    return {
        "task": task.model_dump(mode="json", exclude={"path"}),
        "nodes": serialized,
        "focus_node": focus,
    }


def _resolve_context(
    config: HirakuConfig, context_paths: list[str] | None, context_bytes: int | None
) -> Bundle:
    return gather_context(config.root, context_paths, max_bytes=context_bytes)


# --------------------------------------------------------------------------- draft task


def draft_state(config: HirakuConfig, *, task_id: str | None = None) -> dict[str, Any]:
    """The state an agent sees when drafting a new task: every existing task, for the
    id-collision and novelty checks `prompts/draft.md` asks it to make.

    "Existing task" means a root record (`parent_id is None`) — `hiraku.record`
    itself has no separate task/experiment distinction any more, so this filters
    `list_records()` by hand rather than calling a `list_tasks`-shaped helper that
    no longer exists.
    """
    existing = [
        record for record in list_records(config=config) if record.parent_id is None
    ]
    return {
        "existing_tasks": [
            {
                "id": task.id,
                "goal": task.goal,
                "question": task.question,
                "summary": task.summary,
            }
            for task in existing
        ],
        "requested_id": task_id,
    }


def _remove_empty_task_leaf(path: Any, expected: Any) -> None:
    if path == expected and path.is_dir():
        with suppress(OSError):
            path.rmdir()


_METRIC_DIRECTIONS = {"minimize", "maximize"}
_DIRECTION_ALIASES = {
    "maximise": "maximize",
    "max": "maximize",
    "higher": "maximize",
    "minimise": "minimize",
    "min": "minimize",
    "lower": "minimize",
}


def _sanitize_metrics(data: dict[str, Any]) -> list[dict[str, str]]:
    """Keep only agent-proposed metrics shaped the way `hiraku.record.Metric` requires:
    a non-empty `name` and a `direction` its `Literal["minimize", "maximize"]` will
    accept.

    `draft` is asked to *propose* a direction for every metric (see `prompts/draft.md`),
    so a missing or unrecognisable one is a malformed reply, not an honest gap — but
    `Metric.direction` still has no room for an `**[UNSET: ...]**` marker, so such a
    metric is dropped rather than guessed, and `_protocol_section`'s caller reports it.
    Common spellings (`max`, `maximise`, `higher`) are normalised; `role` and
    `rationale` are not part of `Metric` and live in the record body instead.
    """
    raw = data.get("metrics")
    if not isinstance(raw, list):
        return []
    metrics: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        direction = str(item.get("direction") or "").strip().lower()
        direction = _DIRECTION_ALIASES.get(direction, direction)
        if not name or direction not in _METRIC_DIRECTIONS or name in seen:
            continue
        metrics.append({"name": name, "direction": direction})
        seen.add(name)
    return metrics


PROTOCOL_HEADING = "Proposed protocol"
DECISION_RULE_HEADING = "Decision rule"

# Written when a draft reply carries no `decision_rule`, so every task has one:
# `analyze` reads this section, and a task cannot be amended freely once experiments
# exist.
DEFAULT_DECISION_RULE = (
    "_No rule was given, so this default applies. Revise it with "
    "`hiraku-agent update` before the first verdict._\n\n"
    "- **Target**: the metric in `## Proposed protocol` closest to the end goal.\n"
    "- **Guards**: the other quality metrics; none may get meaningfully worse.\n"
    "- **Report only**: cost metrics (time, memory).\n"
    "- **Tolerance**: twice the baseline's seed-to-seed standard deviation, from at "
    "least three baseline seeds.\n"
    "- **Verdict against a baseline**: `supports` if the target is better beyond "
    "tolerance and no guard is worse beyond it; `refutes` if the target is worse "
    "beyond tolerance; otherwise `inconclusive`. If it is hard to tell, report a "
    "candidate result and lean `inconclusive`.\n"
    "- **Comparing methods**: drop methods whose guards got worse, rank the rest by "
    "the target, treat differences within tolerance as ties, list cost trade-offs "
    "separately."
)


def _protocol_section(data: dict[str, Any], metrics: list[dict[str, str]]) -> str:
    """The body text of `## Proposed protocol`: every metric and default the agent chose
    on the researcher's behalf, each with its rationale.

    Written into the record body, not only `metrics`, so a reviewer can tell what was
    stated from what was proposed — and so `hiraku-agent update` has one stable place to
    revise.
    """
    lines = [
        "_Chosen by the agent where the instruction left the protocol open. "
        "Edit freely, or revise with `hiraku-agent update`._",
        "",
    ]
    kept = {metric["name"]: metric for metric in metrics}
    raw = data.get("metrics")
    metric_lines: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name not in kept:
            continue
        role = str(item.get("role") or "").strip()
        why = str(item.get("rationale") or "").strip()
        detail = (
            f"{kept[name]['direction']}, {role}" if role else kept[name]["direction"]
        )
        line = f"- `{name}` ({detail})"
        metric_lines.append(f"{line} — {why}" if why else line)
    if metric_lines:
        lines += ["**Metrics**", "", *metric_lines, ""]
    default_lines: list[str] = []
    defaults = data.get("proposed_defaults")
    for item in defaults if isinstance(defaults, list) else []:
        if not isinstance(item, dict) or not item.get("item"):
            continue
        line = (
            f"- **{str(item['item']).strip()}**: {str(item.get('value') or '').strip()}"
        )
        why = str(item.get("rationale") or "").strip()
        label = _ASSUMPTION_LABEL.get(str(item.get("status") or "").strip().lower())
        if label:
            why = f"{label}; {why}" if why else label
        default_lines.append(f"{line} — {why}" if why else line)
    if default_lines:
        lines += ["**Defaults**", "", *default_lines, ""]
    candidates = _candidate_methods_lines(data.get("candidate_methods"))
    if candidates:
        lines += ["**Candidate methods**", "", *candidates, ""]
    if not metric_lines and not default_lines and not candidates:
        return ""
    return "\n".join(lines).rstrip()


CANDIDATES_SPECIFIED = "specified"
CANDIDATES_UNSPECIFIED = "unspecified"


def _candidate_methods_lines(raw: Any) -> list[str]:
    """The `**Candidate methods**` lines of a benchmark-type task, or none.

    Records whether the researcher named the methods to compare. `design` reads this to
    decide between building one node per named method and stopping at a baseline until
    `discover` has found candidates, so it must say which case applies.
    """
    if not isinstance(raw, dict):
        return []
    status = str(raw.get("status") or "").strip().lower()
    methods = [
        str(item).strip()
        for item in (raw.get("methods") if isinstance(raw.get("methods"), list) else [])
        if str(item).strip()
    ]
    if status == CANDIDATES_SPECIFIED and methods:
        return [
            f"- Status: {CANDIDATES_SPECIFIED} by the researcher "
            f"({len(methods)} methods).",
            *[f"- {method}" for method in methods],
        ]
    if status == CANDIDATES_UNSPECIFIED:
        return [
            f"- Status: {CANDIDATES_UNSPECIFIED} — the instruction compares methods "
            "without naming them. Run `hiraku-agent discover` to find candidates "
            "before `design` builds the comparison."
        ]
    return []


def _existing_ids(config: HirakuConfig) -> set[str]:
    return {record.id for record in list_records(config=config)}


def ensure_ids_free(
    ids: list[str], *, config: HirakuConfig, label: str = "record"
) -> None:
    """Refuse the whole batch if any id is already taken or repeats within the batch.

    Ids are unique across the whole project (see `hiraku.record.Record.create`), not
    just within one task. `Record.create` already rejects a taken id, but a workflow
    that creates several records would then die half-way with some written and some
    not; this checks every id up front so a rejected reply writes nothing.
    """
    taken = _existing_ids(config)
    clashes = sorted({item for item in ids if item in taken})
    if clashes:
        raise AgentError(
            f"The {label} id(s) {', '.join(repr(c) for c in clashes)} already exist in "
            f"this project. Nothing was written. Pick different ids (ids are unique "
            f"project-wide), or revise the existing record with "
            f"`hiraku-agent update <id>` if it has no experiments or runs yet."
        )
    repeated = sorted({item for item in ids if ids.count(item) > 1})
    if repeated:
        raise AgentError(
            f"The reply uses the same {label} id more than once: "
            f"{', '.join(repr(r) for r in repeated)}. Nothing was written."
        )


@dataclass
class _NewNode:
    """One node a workflow is about to create: its id, the parent it names (None for
    directly under the task), and a callable that creates and saves it."""

    id: str
    parent: str | None
    build: Callable[[], Node]


def _create_nodes(
    task: Record,
    new: list[_NewNode],
    *,
    config: HirakuConfig,
    label: str = "experiment",
) -> list[Node]:
    """Create a batch of nodes all-or-nothing.

    Everything is checked before the first file is written: every id is valid and free,
    every parent is the task, a node already in this task, or another node of the same
    batch (never a node of another task), and the batch has no cycle. Nodes are then
    created parents first, whatever order the reply listed them in. If a write still
    fails part-way, the nodes this call already wrote are removed again.
    """
    ids = [item.id for item in new]
    for identifier in ids:
        validate_identifier(identifier, label=f"{label} ID")
    ensure_ids_free(ids, config=config, label=label)
    batch = set(ids)
    allowed = {task.id, *(node.id for node in list_nodes(task=task, config=config))}
    stray = [
        f"{item.id!r} -> {item.parent!r}"
        for item in new
        if item.parent and item.parent not in allowed | batch
    ]
    if stray:
        raise AgentError(
            f"The reply names parents outside task {task.id!r}: {', '.join(stray)}. "
            "A parent must be the task, one of its experiments, or another experiment "
            "of the same reply. Nothing was written."
        )
    graph = {item.id: ({item.parent} & batch) - {item.id} for item in new}
    try:
        order = list(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        raise AgentError(
            f"The reply's parents form a cycle ({exc.args[1]}). Nothing was written."
        ) from exc
    if any(item.parent == item.id for item in new):
        raise AgentError("An experiment cannot be its own parent. Nothing was written.")
    by_id = {item.id: item for item in new}
    created: list[Node] = []
    try:
        for identifier in order:
            created.append(by_id[identifier].build())
    except Exception as exc:
        for node in reversed(created):
            with suppress(Exception):
                node.delete(config=config)
            with suppress(OSError):
                node.path.unlink()
        raise AgentError(
            f"Writing the {label}s failed part-way ({exc}); the "
            f"{len(created)} already written were removed again. Nothing was written."
        ) from exc
    return created


def _create_task(
    task_id: str,
    data: dict[str, Any],
    *,
    branch: str | None,
    activate: bool,
    config: HirakuConfig,
) -> Record:
    """Create a task — a root `Record` — from a `draft` reply, the same way `hiraku
    record create ... --type task` does by hand: resolve and validate the branch name,
    persist the record, then bind the git branch — rolling the record back if the
    branch step fails, so the two never diverge.
    """
    fields: dict[str, Any] = {
        key: str(data[key]) for key in ("goal", "question", "summary") if data.get(key)
    }
    metrics = _sanitize_metrics(data)
    if metrics:
        fields["metrics"] = metrics
    body = str(data.get("body") or "")
    protocol = _protocol_section(data, metrics)
    if protocol:
        body = sections.set_section(body, PROTOCOL_HEADING, protocol)
    rule = str(data.get("decision_rule") or "").strip() or DEFAULT_DECISION_RULE
    body = sections.set_section(body, DECISION_RULE_HEADING, rule)
    selected_branch = (
        branch if branch is not None else config.branch.format(record_id=task_id)
    )
    repository = GitRepository(config)
    repository.validate_branch(selected_branch)
    created = Record.create(
        task_id,
        parent_id=None,
        type="task",
        markdown=body,
        activate=False,
        config=config,
        branch=selected_branch,
        **fields,
    )
    created_leaf = created.path.parent
    try:
        repository.create_record_branch(task_id, branch=selected_branch)
    except Exception:
        created.delete(config=config)
        _remove_empty_task_leaf(created_leaf, config.records_dir / task_id)
        raise
    if activate:
        config.set_active_record(created)
    return created


def run_draft(
    instruction: str,
    *,
    task_id: str | None = None,
    command: list[str] | None = None,
    dry_run: bool = False,
    apply: bool = True,
    context_paths: list[str] | None = None,
    repo_paths: list[str] | None = None,
    context_bytes: int | None = None,
    unset_env: list[str] | None = None,
    hooks: Hooks | None = None,
    timeout: int | None = None,
    branch: str | None = None,
    activate: bool = True,
    action: str | None = None,
    context_id: str | None = None,
    config: HirakuConfig | None = None,
) -> AgentRun:
    """Turn free-text instruction into a new task (a root `Record`) — the same effect
    as running `hiraku record create ... --type task` by hand.

    Unlike `design`/`discover`/`triage`/`analyze`, there is no task to load first: this
    workflow creates one. A `task_id` the caller already knows (`--task-id`) is
    validated before the agent is even called, so a bad id fails fast rather than after
    a paid call; an id the agent proposes instead is validated once the reply is in,
    exactly as `hiraku record create` validates whatever id it is given.

    `action`, if given, needs `task_id` too — an `Action` (`hiraku.agent.Action`) is
    scoped to a task id (`entity`), and there is no task id to scope it to until this
    call has already picked one (see `actions.py`).
    """
    if not (instruction or "").strip():
        raise AgentError("An instruction is required to draft a task.")
    selected = config or HirakuConfig.discover()
    if task_id is not None:
        validate_identifier(task_id, label="task ID")
        # Before the (paid) agent call, not after: `draft` never overwrites a record.
        ensure_ids_free([task_id], config=selected, label="task")
    command, timeout, context_paths = apply_overrides(
        action,
        context_id,
        entity=task_id,
        command=command,
        timeout=timeout,
        context_paths=context_paths,
        config=selected,
    )
    command = with_repos(command, repo_paths)
    bundle = _resolve_context(selected, context_paths, context_bytes)
    prompt = build_prompt(
        "draft",
        attach_context(draft_state(selected, task_id=task_id), bundle),
        instruction=instruction,
        root=selected.root,
        repos=repo_paths,
    )
    result = AgentRun(workflow="draft", prompt=prompt, context=bundle)
    if dry_run:
        return result

    result.raw, result.log, result.seconds = invoke(
        selected,
        "draft",
        task_id or "new-task",
        prompt,
        bundle,
        command=command,
        timeout=timeout,
        hooks=hooks,
        unset_env=unset_env,
    )
    result.data = extract_json(result.raw)
    resolved_id = task_id or _require(result.data, "id", str)
    if task_id is None:
        validate_identifier(resolved_id, label="task ID")
    if not apply:
        return result
    ensure_ids_free([resolved_id], config=selected, label="task")

    created = _create_task(
        resolved_id, result.data, branch=branch, activate=activate, config=selected
    )
    result.written.append(created.path)
    return result


# ------------------------------------------------------------------------------- design


def run_design(
    task_id: str,
    *,
    instruction: str = "",
    command: list[str] | None = None,
    dry_run: bool = False,
    apply: bool = True,
    context_paths: list[str] | None = None,
    repo_paths: list[str] | None = None,
    context_bytes: int | None = None,
    unset_env: list[str] | None = None,
    hooks: Hooks | None = None,
    timeout: int | None = None,
    action: str | None = None,
    context_id: str | None = None,
    config: HirakuConfig | None = None,
) -> AgentRun:
    """Propose a tree of experiments for a task. Writes proposed `Node`s (no commit)."""
    selected = config or HirakuConfig.discover()
    task = load_record(task_id, config=selected)
    nodes = list_nodes(task=task, config=selected)
    command, timeout, context_paths = apply_overrides(
        action,
        context_id,
        entity=task.id,
        command=command,
        timeout=timeout,
        context_paths=context_paths,
        config=selected,
    )
    command = with_repos(command, repo_paths)
    bundle = _resolve_context(selected, context_paths, context_bytes)
    prompt = build_prompt(
        "design",
        attach_context(task_state(task, nodes, config=selected), bundle),
        instruction=instruction,
        root=selected.root,
        repos=repo_paths,
    )
    result = AgentRun(workflow="design", prompt=prompt, context=bundle)
    if dry_run:
        return result

    result.raw, result.log, result.seconds = invoke(
        selected,
        "design",
        task_id,
        prompt,
        bundle,
        command=command,
        timeout=timeout,
        hooks=hooks,
        unset_env=unset_env,
    )
    result.data = extract_json(result.raw)
    proposed = _require(result.data, "experiments", list)
    if not bundle.files and not repo_paths:
        result.data["inputs_note"] = (
            "this run read no --context/--context-id file and no --repo, only the task "
            "record and the node tree; values that live in your code or data (paths, "
            "thresholds, configs) could not be looked up. Re-run with --repo / "
            "--context-id, or fill them in with `hiraku-agent update <id> --repo ...`."
        )
    if not apply:
        return result

    def build(item: dict[str, Any]) -> Node:
        node = create_node(
            str(item["id"]),
            task=task,
            parent=str(item["parent"]) if item.get("parent") else None,
            kind=str(item.get("kind") or "hypothesis"),
            origin=_origin(item.get("origin"), default="agent-diagnostic"),
            expectation=str(item.get("expectation") or ""),
            config=selected,
        )
        _write_sections(
            node,
            item,
            {
                "scope": "Scope",
                "expectation_body": "Expectation detail",
                "rationale": "Rationale",
            },
        )
        open_items = _open_items_section(item.get("open_items"))
        if open_items:
            node.markdown = sections.set_section(
                node.markdown, OPEN_ITEMS_HEADING, open_items
            )
        node.save()
        return node

    created = _create_nodes(
        task,
        [
            _NewNode(
                str(item["id"]),
                str(item["parent"]) if item.get("parent") else None,
                lambda item=item: build(item),
            )
            for item in proposed
            if isinstance(item, dict) and item.get("id")
        ],
        config=selected,
    )
    order = {
        str(item["id"]): index
        for index, item in enumerate(proposed)
        if isinstance(item, dict) and item.get("id")
    }
    for node in sorted(created, key=lambda node: order[node.id]):
        result.written.append(node.path)
    return result


def run_plan(
    instruction: str,
    *,
    task_id: str | None = None,
    command: list[str] | None = None,
    dry_run: bool = False,
    apply: bool = True,
    context_paths: list[str] | None = None,
    repo_paths: list[str] | None = None,
    context_bytes: int | None = None,
    unset_env: list[str] | None = None,
    hooks: Hooks | None = None,
    timeout: int | None = None,
    branch: str | None = None,
    activate: bool = True,
    action: str | None = None,
    context_id: str | None = None,
    config: HirakuConfig | None = None,
) -> AgentRun:
    """`draft` then `design` in one go: from a couple of sentences to a task and its
    first experiments.

    Two agent calls, run back to back with the same context and repositories. The task
    is written first, so a failure in the design step leaves a valid task behind and the
    error says so; nothing is rolled back. `--dry-run` shows the draft prompt only: the
    design prompt depends on the task the draft would write. `--no-apply` stops after
    the draft for the same reason.
    """
    selected = config or HirakuConfig.discover()
    shared: dict[str, Any] = {
        "command": command,
        "context_paths": context_paths,
        "repo_paths": repo_paths,
        "context_bytes": context_bytes,
        "unset_env": unset_env,
        "hooks": hooks,
        "timeout": timeout,
        "context_id": context_id,
        "config": selected,
    }
    drafted = run_draft(
        instruction,
        task_id=task_id,
        dry_run=dry_run,
        apply=apply,
        branch=branch,
        activate=activate,
        action=action,
        **shared,
    )
    if dry_run or not apply:
        return drafted
    resolved = task_id or str(drafted.data.get("id") or "")
    try:
        designed = run_design(
            resolved,
            action=action if task_id else None,
            **shared,
        )
    except HirakuError as exc:
        raise AgentError(
            f"Task {resolved!r} was written, but designing its experiments failed: "
            f"{exc}. Re-run `hiraku-agent design --task {resolved}`."
        ) from exc
    data = dict(designed.data)
    data["task"] = resolved
    notes = [
        f"{label}: {note}"
        for label, note in (
            ("task", drafted.data.get("notes")),
            ("experiments", designed.data.get("notes")),
        )
        if note
    ]
    data["notes"] = "\n  ".join(notes) or None
    return AgentRun(
        workflow="plan",
        prompt=drafted.prompt,
        raw=designed.raw,
        data=data,
        written=[*drafted.written, *designed.written],
        context=designed.context,
        log=designed.log,
        seconds=drafted.seconds + designed.seconds,
    )


OPEN_ITEMS_HEADING = "Open items"
# How a value was settled (`prompts/_assumptions.md`): looked up, conventional default,
# a recommended pick between real options, or a gap the agent could not fill.
_OPEN_ITEM_STATUS = {
    "found": "found in the project",
    "default": "default, to be calibrated",
    "choice": "recommended choice, change if you disagree",
    "unset": "unset, needs the researcher",
    # Older replies used `decide` for what is now a recommended choice.
    "decide": "recommended choice, change if you disagree",
}
_ASSUMPTION_LABEL = {
    "found": "found in the project",
    "default": "default, initial value",
    "choice": "recommended choice",
    "unset": "unset, needs the researcher",
}


def _open_items_section(raw: Any) -> str:
    """`## Open items` for a node: each value the plan depends on that the task did not
    give, with where it came from.

    `found` is read from a context or repo file (and names it), `default` is an initial
    value with a stated rationale, `choice` is the agent's recommended pick between real
    options, `unset` is a gap only the researcher can fill. Written into the node so a
    later `update` or `implement` has one place to resolve them, instead of them living
    only in the run's `notes`.
    """
    lines: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not str(item.get("item") or "").strip():
            continue
        status = str(item.get("status") or "").strip().lower()
        label = _OPEN_ITEM_STATUS.get(status, "unresolved")
        value = str(item.get("value") or "").strip() or "_not determined_"
        source = str(item.get("source") or "").strip()
        line = f"- **{str(item['item']).strip()}** — {value} ({label}"
        line += f"; {source})" if source else ")"
        lines.append(line)
    return "\n".join(lines)


# ----------------------------------------------------------------------------- discover


def run_discover(
    task_id: str,
    *,
    on: str | None = None,
    findings_only: bool = False,
    instruction: str = "",
    command: list[str] | None = None,
    dry_run: bool = False,
    apply: bool = True,
    context_paths: list[str] | None = None,
    repo_paths: list[str] | None = None,
    context_bytes: int | None = None,
    unset_env: list[str] | None = None,
    hooks: Hooks | None = None,
    timeout: int | None = None,
    action: str | None = None,
    context_id: str | None = None,
    config: HirakuConfig | None = None,
) -> AgentRun:
    """Search for candidate hypotheses, and/or investigate what already exists.

    `on` names the record the call is about — the task itself (the default) or any node
    beneath it. Proposals respond to it (a proposal with no `proposed_parent` is placed
    under it), and the `investigation` the agent returns — cited findings, inferences
    labelled as unverified, open questions — is appended to *that record's own file*
    under `## Investigation`, one dated entry per call, so nothing is overwritten and
    the reasoning sits next to the node it is about rather than in a separate file.

    `findings_only` asks for the investigation alone: no node is proposed or declined.
    Reading a project's files, logs or code needs `--repo` or `--context`; without
    either, the findings are marked unverified (see `engine.files_readable`).

    Writes accepted ideas as proposed `Node`s, declined ones as `abandoned` `Node`s —
    see this module's docstring for why both are `Node`s rather than a separate record
    type.
    """
    selected = config or HirakuConfig.discover()
    task = load_record(task_id, config=selected)
    focus_id = on or task.id
    if on is not None:
        focus_record = load_record(on, config=selected)
        if task_of(focus_record, config=selected).id != task.id:
            raise HirakuError(f"'{on}' does not belong to task '{task.id}'.")
    nodes = list_nodes(task=task, config=selected)
    command, timeout, context_paths = apply_overrides(
        action,
        context_id,
        entity=task.id,
        command=command,
        timeout=timeout,
        context_paths=context_paths,
        config=selected,
    )
    command = with_repos(command, repo_paths)
    bundle = _resolve_context(selected, context_paths, context_bytes)
    state = task_state(
        task, nodes, config=selected, focus=None if focus_id == task.id else focus_id
    )
    state["focus"] = {
        "id": focus_id,
        "kind": "task" if focus_id == task.id else "experiment",
    }
    state["mode"] = "findings_only" if findings_only else "propose_and_investigate"
    prompt = build_prompt(
        "discover",
        attach_context(state, bundle),
        instruction=instruction,
        root=selected.root,
        repos=repo_paths,
    )
    result = AgentRun(workflow="discover", prompt=prompt, context=bundle)
    if dry_run:
        return result

    result.raw, result.log, result.seconds = invoke(
        selected,
        "discover",
        focus_id,
        prompt,
        bundle,
        command=command,
        timeout=timeout,
        hooks=hooks,
        unset_env=unset_env,
    )
    result.data = extract_json(result.raw)
    investigation = result.data.get("investigation")
    raw_proposals = result.data.get("proposals")
    if findings_only or (not isinstance(raw_proposals, list) and investigation):
        proposals: list[Any] = []
    else:
        proposals = _require(result.data, "proposals", list)
    if not apply:
        return result

    # A proposal whose citations were recalled rather than read says so in the record
    # itself. The terminal warning scrolls away; the reviewer deciding on this proposal
    # reads the file.
    resolved_command = command or agent_command()
    unverified = not web_allowed(resolved_command) or web_was_denied(result.raw)
    result.data["citations_unverified"] = unverified

    # An id already on disk is left alone: a declined proposal is the record of a
    # decision, and re-running discover must not overwrite it or die half-way through.
    existing_ids = _existing_ids(selected)  # ids are unique project-wide
    skipped: list[str] = []
    batch: list[_NewNode] = []
    declined: list[str] = []

    def build_proposal(item: dict[str, Any], parent: str | None) -> Node:
        node = create_node(
            str(item["id"]),
            task=task,
            parent=parent,
            kind=str(item.get("kind") or "hypothesis"),
            origin=_origin(item.get("origin"), default="agent-literature"),
            expectation=str(item.get("expectation") or ""),
            config=selected,
        )
        provenance = str(item.get("provenance") or "")
        if unverified and provenance:
            provenance = f"{UNVERIFIED_NOTE}\n\n{provenance}"
        if item.get("title"):
            node.markdown = sections.set_section(
                node.markdown, "Title", str(item["title"])
            )
        _write_sections(
            node,
            {**item, "provenance": provenance},
            {
                "provenance": "Provenance",
                "applicability": "Applicability",
                "prediction": "Prediction",
                "investigation_test": "Investigation test",
                "cost": "Cost",
                "counterargument": "Counterargument",
            },
        )
        node.save()
        return node

    def build_declined(item: dict[str, Any]) -> Node:
        node = create_node(
            str(item["id"]),
            task=task,
            kind=str(item.get("kind") or "hypothesis"),
            origin=_origin(item.get("origin"), default="agent-literature"),
            expectation=str(item.get("expectation") or ""),
            abandoned=True,
            config=selected,
        )
        if item.get("title"):
            node.markdown = sections.set_section(
                node.markdown, "Title", str(item["title"])
            )
        why_parts = []
        if item.get("failing_condition"):
            why_parts.append(f"**Failing condition:** {item['failing_condition']}")
        if item.get("reason"):
            why_parts.append(str(item["reason"]))
        if why_parts:
            node.markdown = sections.set_section(
                node.markdown, "Why declined", "\n\n".join(why_parts)
            )
        if item.get("belongs_under"):
            node.markdown = sections.set_section(
                node.markdown, "Belongs under", str(item["belongs_under"])
            )
        node.save()
        return node

    # An id already on disk is left alone: a declined proposal is the record of a
    # decision, and re-running discover must not overwrite it or die half-way through.
    for item in proposals:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        if str(item["id"]) in existing_ids:
            skipped.append(str(item["id"]))
            continue
        parent = (
            str(item["proposed_parent"])
            if item.get("proposed_parent")
            else (focus_id if focus_id != task.id else None)
        )
        batch.append(
            _NewNode(
                str(item["id"]),
                parent,
                lambda item=item, parent=parent: build_proposal(item, parent),
            )
        )

    # An idea considered and rejected is a record too — otherwise the reasoning lives in
    # terminal scrollback, and next month's discover proposes the same thing again.
    for item in [] if findings_only else result.data.get("declined") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        if str(item["id"]) in existing_ids:
            skipped.append(str(item["id"]))
            continue
        declined.append(str(item["id"]))
        batch.append(
            _NewNode(str(item["id"]), None, lambda item=item: build_declined(item))
        )

    created = _create_nodes(task, batch, config=selected)
    order = {item.id: index for index, item in enumerate(batch)}
    for node in sorted(created, key=lambda node: order[node.id]):
        result.written.append(node.path)

    result.data["declined_written"] = declined
    result.data["skipped_existing"] = skipped

    entry = _investigation_entry(
        investigation,
        instruction,
        files_unverified=not files_readable(command, bundle),
    )
    if entry:
        focus = load_node(focus_id, config=selected)
        focus.markdown = sections.append_section(
            focus.markdown, INVESTIGATION_HEADING, entry
        )
        focus.save()
        result.written.append(focus.path)
        result.data["investigation_written_to"] = focus_id
    return result


INVESTIGATION_HEADING = "Investigation"


def _investigation_entry(
    investigation: Any, instruction: str, *, files_unverified: bool
) -> str:
    """One dated entry for `## Investigation`, or `""` when there is nothing to record.

    Appended, never replacing: a node can be investigated more than once (with a
    different focus each time), and each entry stays as the record of what was asked and
    what came back. When the agent had no way to read the project's files, its findings
    are prefixed with `UNVERIFIED_FILES_NOTE`.
    """
    if not isinstance(investigation, dict):
        return ""
    parts: list[str] = []
    for key, label in (
        (
            "findings",
            "**Established** (from files, logs, code or sources — each cited)",
        ),
        ("inferences", "**Inferred, not verified**"),
        ("open_questions", "**Open questions**"),
    ):
        text = str(investigation.get(key) or "").strip()
        if text:
            parts.append(f"{label}\n\n{text}")
    if not parts:
        return ""
    focus = " ".join((instruction or "").split())[:80] or "investigation"
    heading = f"### {_dt.date.today().isoformat()} — {focus}"
    note = [UNVERIFIED_FILES_NOTE] if files_unverified else []
    return "\n\n".join([heading, *note, *parts])


# -------------------------------------------------------------------------- implement


def run_implement(
    node_id: str,
    *,
    task_id: str | None = None,
    instruction: str = "",
    command: list[str] | None = None,
    dry_run: bool = False,
    apply: bool = True,
    context_paths: list[str] | None = None,
    context_bytes: int | None = None,
    unset_env: list[str] | None = None,
    hooks: Hooks | None = None,
    timeout: int | None = None,
    action: str | None = None,
    context_id: str | None = None,
    config: HirakuConfig | None = None,
) -> AgentRun:
    """Write the real code for an approved proposal, then bind the resulting commit.

    The one workflow in this module where the agent writes files — see `engine.py`'s
    `DEFAULT_IMPLEMENT_AGENT_CMD` and `prompts/implement.md`'s "Exception to the rule
    above" for why that is scoped narrowly and separately from everything else here.

    The approval gate is checked here too, not only in `schedule.suggest_next` (which
    only *names* `implement` once `is_approved` is already true): this function is also
    reachable directly from `hiraku-agent implement <id>`, bypassing the scheduler
    entirely, so the gate has to hold at the point where code actually gets written, not
    only at the point where the scheduler makes a suggestion. In `guided` mode this is
    the human trigger the rest of this workflow's write access depends on; in `auto`
    mode `is_approved` is already unconditionally true (`gates.is_approved`).
    """
    selected = config or HirakuConfig.discover()
    node = load_node(node_id, task=task_id, config=selected)
    if node.abandoned:
        raise HirakuError(f"'{node.id}' is abandoned — nothing to implement.")
    if not node.is_proposal:
        raise HirakuError(
            f"'{node.id}' already has a commit bound ({node.commit}) — nothing to "
            f"implement. Use `hiraku record update` directly if you meant to "
            f"rebind it."
        )
    if not is_approved(node):
        raise AgentError(
            f"'{node.id}' is not approved yet. Run `hiraku-agent approve {node.id} "
            f"--task {task_of(node, config=selected).id} --by <you>` first — in guided "
            f"mode that approval is what authorizes this workflow to write real code."
        )

    task = task_of(node, config=selected)
    nodes = list_nodes(task=task, config=selected)
    command, timeout, context_paths = apply_overrides(
        action,
        context_id,
        entity=task.id,
        command=command,
        timeout=timeout,
        context_paths=context_paths,
        config=selected,
    )
    bundle = _resolve_context(selected, context_paths, context_bytes)
    prompt = build_prompt(
        "implement",
        attach_context(task_state(task, nodes, config=selected, focus=node.id), bundle),
        instruction=instruction,
        root=selected.root,
    )
    result = AgentRun(workflow="implement", prompt=prompt, context=bundle)
    if dry_run:
        return result

    # Unlike every other workflow, an unresolved `command` here must NOT fall back to
    # `agent_command()`'s plain `claude -p`: that default denies file edits
    # non-interactively (see `engine.KNOWN_FAILURES`), so this call would run but could
    # never actually implement anything. Resolve the implement-specific, separately
    # named default instead — still overridable the same way everything else is
    # (`--agent-cmd`, a bound `Action`'s `agent_cmd`, or this one's own env var).
    resolved_command = command or agent_command(
        env_var=IMPLEMENT_AGENT_CMD_ENV_VAR, default=DEFAULT_IMPLEMENT_AGENT_CMD
    )

    # The HEAD this call starts from: the reported commit must be newer than it.
    try:
        start_head: str | None = GitRepository(selected).resolve("HEAD")
    except HirakuError:
        start_head = None  # not a repository yet, or no commit yet

    try:
        result.raw, result.log, result.seconds = invoke(
            selected,
            "implement",
            node.id,
            prompt,
            bundle,
            command=resolved_command,
            timeout=timeout,
            hooks=hooks,
            unset_env=unset_env,
        )
    except AgentTimeout as exc:
        # The agent was stopped mid-edit. Nothing is bound and nothing is discarded:
        # say what it left behind so a person can keep or drop it.
        left = _working_tree_status(selected)
        raise AgentTimeout(
            f"{exc}\n'{node.id}' was not bound to any commit. "
            + (
                "The agent left these uncommitted changes (kept, not discarded):\n"
                + left
                if left
                else "The working tree has no uncommitted changes."
            )
        ) from exc
    result.data = extract_json(result.raw)
    data = result.data
    if data.get("blocked") or not apply:
        return result

    raw_commit = _require(data, "commit", str)
    repository = GitRepository(selected)
    resolved_commit = repository.resolve(raw_commit)
    if start_head is not None and (
        resolved_commit == start_head
        or repository.is_ancestor(resolved_commit, start_head)
    ):
        raise AgentError(
            f"The agent reported commit {raw_commit!r} ({resolved_commit[:12]}), but "
            f"that commit already existed before this call (HEAD was "
            f"{start_head[:12]}), so it cannot contain this node's implementation. "
            f"'{node.id}' was not bound. Check `git status` and `git log` for what the "
            f"agent did."
        )

    # Reload: an agent call that just edited and committed files is exactly the case
    # `run_triage`/`run_analyze`'s own "an agent call is minutes long" reload guards
    # against, and here the stakes are higher — binding a commit is the one write in
    # this module that is not easily reverted by editing a markdown file back.
    live = load_node(node.id, task=task, config=selected)
    if not live.is_proposal:
        raise HirakuError(
            f"'{live.id}' already has a commit bound ({live.commit}) — something else "
            f"implemented it while this call was running. Not overwriting; inspect "
            f"both commits by hand."
        )

    entrypoints = data.get("entrypoints")
    if isinstance(entrypoints, dict) and entrypoints:
        live.entrypoints = {**live.entrypoints, **entrypoints}

    if data.get("notes"):
        live.markdown = sections.set_section(
            live.markdown, "Implementation notes", str(data["notes"])
        )

    live.commit = resolved_commit
    live.save()
    result.written.append(live.path)
    return result


def _working_tree_status(config: HirakuConfig) -> str:
    """`git status --short` of the project, or "" if it is clean or unreadable."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(config.root), "status", "--short"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


# ------------------------------------------------------------------------------- triage


def run_triage(
    node_id: str,
    *,
    task_id: str | None = None,
    instruction: str = "",
    command: list[str] | None = None,
    dry_run: bool = False,
    apply: bool = True,
    context_paths: list[str] | None = None,
    repo_paths: list[str] | None = None,
    context_bytes: int | None = None,
    unset_env: list[str] | None = None,
    hooks: Hooks | None = None,
    timeout: int | None = None,
    action: str | None = None,
    context_id: str | None = None,
    config: HirakuConfig | None = None,
) -> AgentRun:
    """Judge whether a concluded node is worth going further on, and if so, how.

    Runs on a `concluded` node regardless of which way its verdict went — a
    `supports` can still be worth a robustness check or a natural ablation, exactly
    as a `refutes`/`inconclusive` can still be worth a diagnostic; see
    `prompts/triage.md` for the two different lenses it applies depending on which
    kind of verdict it's given. On success (not `blocked`), marks the node
    `triaged`, which — together with `abandoned` and having children — is one of
    the ways a node reaches `completed` (see `state.node_state`).

    Expects the node already has a verdict-worthy situation (usually
    `awaiting_analysis` or `concluded` — see `docs/state.md`); it does not check
    the derived state itself, since a researcher may reasonably triage a node the
    scheduler hasn't gotten to yet.
    """
    selected = config or HirakuConfig.discover()
    node = load_node(node_id, task=task_id, config=selected)
    task = task_of(node, config=selected)
    nodes = list_nodes(task=task, config=selected)
    command, timeout, context_paths = apply_overrides(
        action,
        context_id,
        entity=task.id,
        command=command,
        timeout=timeout,
        context_paths=context_paths,
        config=selected,
    )
    command = with_repos(command, repo_paths)
    bundle = _resolve_context(selected, context_paths, context_bytes)
    prompt = build_prompt(
        "triage",
        attach_context(task_state(task, nodes, config=selected, focus=node.id), bundle),
        instruction=instruction,
        root=selected.root,
        repos=repo_paths,
    )
    result = AgentRun(workflow="triage", prompt=prompt, context=bundle)
    if dry_run:
        return result

    result.raw, result.log, result.seconds = invoke(
        selected,
        "triage",
        node.id,
        prompt,
        bundle,
        command=command,
        timeout=timeout,
        hooks=hooks,
        unset_env=unset_env,
    )
    result.data = extract_json(result.raw)
    data = result.data
    if data.get("blocked") or not apply:
        return result

    # Reload: an agent call is minutes long, and writing from a stale copy would clobber
    # anything changed in the meantime.
    live = load_node(node.id, task=task, config=selected)
    markdown = live.markdown
    if data.get("findings"):
        markdown = sections.set_section(markdown, "Findings", str(data["findings"]))
    if data.get("conclusions"):
        markdown = sections.set_section(
            markdown, "Conclusions", str(data["conclusions"])
        )

    verdict = data.get("verdict")
    failure_class = str(data.get("failure_class") or "").strip().upper()
    if failure_class in {"B", "C"} and verdict in {"refutes", "inconclusive"}:
        live.verdict = verdict

    if failure_class == "A":
        # The runs did not test the expectation, so any verdict drawn from them is void
        # and there is nothing to decide about going further: the node needs a fix, not
        # a triage decision. `schedule.suggest_next` says so until other runs exist.
        run_ids = sorted(str(run.id) for run in live.runs(config=selected))
        live.verdict = None
        live.triaged = False
        live.execution_defect = True
        live.defect_runs = run_ids
        markdown = sections.set_section(
            markdown,
            "Execution defect",
            "\n\n".join(
                part
                for part in (
                    str(data.get("class_evidence") or "").strip(),
                    "Runs that did not test the expectation: "
                    + (", ".join(f"`{run}`" for run in run_ids) or "none"),
                    "Fix the code on this node, bind the new commit and launch again; "
                    "these runs are left out of the next analysis.",
                )
                if part
            ),
        )
        live.markdown = markdown
        live.save()
        result.written.append(live.path)
        return result

    test = data.get("investigation_test") or {}
    if isinstance(test, dict) and test:
        lines = ["**Investigation test.**", ""]
        for key, value in test.items():
            if not isinstance(value, dict):
                continue
            mark = "pass" if value.get("pass") else "FAIL"
            lines.append(f"- `{key}` — {mark}. {value.get('note', '')}".rstrip())
        if not data.get("proceed"):
            blocking = data.get("blocking_condition")
            lines += ["", f"Stopped: condition `{blocking}` failed."]
        markdown = sections.append_section(
            markdown, "Decisions and next steps", "\n".join(lines)
        )

    # Children first, all-or-nothing: if they cannot be written, the node must not be
    # marked as decided either.
    if data.get("proceed"):

        def build(child: dict[str, Any]) -> Node:
            child_node = create_node(
                str(child["id"]),
                task=task,
                parent=live.id,
                kind=str(child.get("kind") or "diagnostic"),
                origin="agent-diagnostic",
                expectation=str(child.get("expectation") or ""),
                mode=live.mode,
                config=selected,
            )
            _write_sections(
                child_node,
                child,
                {"scope": "Scope", "expectation_body": "Expectation detail"},
            )
            child_node.save()
            return child_node

        children = [
            child
            for child in data.get("children") or []
            if isinstance(child, dict) and child.get("id")
        ]
        created = _create_nodes(
            task,
            [
                _NewNode(str(child["id"]), live.id, lambda child=child: build(child))
                for child in children
            ],
            config=selected,
        )
        result.written.extend(node.path for node in created)

    # A completed triage call always records its decision, win or lose — this is
    # what lets `state.node_state` tell "never triaged" from "triaged, decided not
    # to go further" once there are no children to show for it either way.
    live.triaged = True
    live.markdown = markdown
    live.save()
    result.written.insert(0, live.path)
    return result


# ------------------------------------------------------------------------------ analyze


def run_analyze(
    node_id: str,
    *,
    task_id: str | None = None,
    only: list[str] | None = None,
    instruction: str = "",
    command: list[str] | None = None,
    dry_run: bool = False,
    apply: bool = True,
    context_paths: list[str] | None = None,
    repo_paths: list[str] | None = None,
    context_bytes: int | None = None,
    unset_env: list[str] | None = None,
    hooks: Hooks | None = None,
    timeout: int | None = None,
    action: str | None = None,
    context_id: str | None = None,
    allow_running: bool = False,
    config: HirakuConfig | None = None,
) -> AgentRun:
    """Judge a node's runs against its expectation and write its findings — or, given a
    *task* id, compare every node under the task and answer its question.

    Any record id works, resolved from the record itself, the way `run_update` does: a
    root record (`parent_id is None`) is a task and gets `_analyze_task`; anything else
    is a node and is judged here.

    A node's file gets a `## Metric comparison` table (computed by
    `hiraku_agent.compare`, not by the agent), `## Findings`, `## Conclusions`,
    `## Decisions and next steps` and `## Missing evidence`; its `verdict` is set when
    the agent returns one.

    A node whose launch is still in flight is refused (its runs are partial) unless
    `allow_running`, in which case the findings say they are based on partial results.
    Runs `triage` marked as an execution defect (`defect_runs`) are left out of the
    comparison table and the agent is told not to use them. The run ids judged are
    recorded in `analyzed_runs`, so an analysis that reached no verdict is not repeated
    until new runs arrive.
    """
    selected = config or HirakuConfig.discover()
    record = load_record(node_id, config=selected)
    if only and record.parent_id is not None:
        raise HirakuError("--nodes applies to a task: pass a task id, not a node id.")
    if record.parent_id is None:
        return _analyze_task(
            record,
            only=only,
            instruction=instruction,
            command=command,
            dry_run=dry_run,
            apply=apply,
            context_paths=context_paths,
            repo_paths=repo_paths,
            context_bytes=context_bytes,
            unset_env=unset_env,
            hooks=hooks,
            timeout=timeout,
            action=action,
            context_id=context_id,
            config=selected,
        )
    node = load_node(node_id, task=task_id, config=selected)
    if not node.runs(config=selected):
        raise HirakuError(f"'{node.id}' has no runs to analyse.")
    task = task_of(node, config=selected)
    nodes = list_nodes(task=task, config=selected)
    command, timeout, context_paths = apply_overrides(
        action,
        context_id,
        entity=task.id,
        command=command,
        timeout=timeout,
        context_paths=context_paths,
        config=selected,
    )
    command = with_repos(command, repo_paths)
    bundle = _resolve_context(selected, context_paths, context_bytes)
    runs_by_id, states, _ = _node_bookkeeping(nodes, config=selected)
    partial = states.get(node.id) == "running"
    if partial and not allow_running:
        raise HirakuError(
            f"'{node.id}' is still running, so its runs are partial. Wait for the "
            "launch to end, or pass --allow-running to analyse what is there so far."
        )
    defective = set(node.defect_runs or [])
    if defective:
        runs_by_id[node.id] = [
            run for run in runs_by_id[node.id] if str(run.id) not in defective
        ]
    judged = sorted(str(run.id) for run in runs_by_id[node.id])
    comparison = compare.compare_to_baselines(task, nodes, runs_by_id, focus=node.id)
    state = task_state(task, nodes, config=selected, focus=node.id)
    if comparison:
        state["metric_comparison"] = comparison.to_state()
    prompt = build_prompt(
        "analyze",
        attach_context(state, bundle),
        instruction=instruction,
        root=selected.root,
        repos=repo_paths,
    )
    result = AgentRun(workflow="analyze", prompt=prompt, context=bundle)
    if dry_run:
        return result

    result.raw, result.log, result.seconds = invoke(
        selected,
        "analyze",
        node.id,
        prompt,
        bundle,
        command=command,
        timeout=timeout,
        hooks=hooks,
        unset_env=unset_env,
    )
    result.data = extract_json(result.raw)
    if not apply:
        return result

    live = load_node(node.id, task=task, config=selected)
    markdown = live.markdown
    if comparison:
        markdown = sections.set_section(
            markdown, "Metric comparison", compare.comparison_markdown(comparison)
        )
    markdown = _write_analysis_sections(markdown, result.data)
    if partial:
        findings = sections.read_section(markdown, "Findings") or ""
        markdown = sections.set_section(
            markdown,
            "Findings",
            f"{PARTIAL_NOTE}\n\n{findings}".rstrip(),
        )
    verdict = result.data.get("verdict")
    if verdict in {"supports", "refutes", "inconclusive"}:
        live.verdict = verdict
        # A fresh verdict needs a fresh go/no-go decision — don't let a `triaged`
        # flag earned by a *previous* verdict make this node look already decided.
        live.triaged = False
        # New runs were judged: an earlier execution defect has been dealt with.
        live.execution_defect = False
    live.analyzed_runs = judged
    candidate = result.data.get("promotable_candidate")
    if candidate:
        markdown = sections.append_section(
            markdown,
            "Decisions and next steps",
            f"Candidate run: `{candidate}` — worth a person looking at as a possible "
            f"new reference.",
        )
    live.markdown = markdown
    live.save()
    result.written.append(live.path)
    return result


PARTIAL_NOTE = "_Partial results: the launch was still running when this was analysed._"


_ANALYSIS_SECTIONS = (
    ("findings", "Findings"),
    ("conclusions", "Conclusions"),
    ("next_steps", "Decisions and next steps"),
)
_TASK_ANALYSIS_SECTIONS = (
    ("findings", "Findings"),
    ("insights", "Cross-node insights"),
    ("conclusions", "Conclusions"),
    ("next_steps", "Decisions and next steps"),
)


def _write_analysis_sections(
    markdown: str,
    data: dict[str, Any],
    plan: tuple[tuple[str, str], ...] = _ANALYSIS_SECTIONS,
) -> str:
    """The prose sections an `analyze` reply carries, plus `## Missing evidence`.

    `missing_evidence` used to be printed to the terminal and lost; it is a section of
    its own, rather than a line appended to `## Decisions and next steps`, so a re-run
    replaces it instead of stacking a second copy. An agent that now reports nothing
    missing clears a stale one.
    """
    for key, heading in plan:
        if data.get(key):
            markdown = sections.set_section(markdown, heading, str(data[key]))
    missing = data.get("missing_evidence")
    if missing:
        markdown = sections.set_section(markdown, "Missing evidence", str(missing))
    elif sections.read_section(markdown, "Missing evidence"):
        markdown = sections.set_section(
            markdown, "Missing evidence", "None identified in the latest analysis."
        )
    return markdown


def _excerpt(markdown: str, heading: str, limit: int) -> str:
    return sections.read_section(markdown, heading)[:limit]


def _analyze_task(
    task: Record,
    *,
    only: list[str] | None,
    instruction: str,
    command: list[str] | None,
    dry_run: bool,
    apply: bool,
    context_paths: list[str] | None,
    repo_paths: list[str] | None,
    context_bytes: int | None,
    unset_env: list[str] | None,
    hooks: Hooks | None,
    timeout: int | None,
    action: str | None,
    context_id: str | None,
    config: HirakuConfig,
) -> AgentRun:
    """Task-level `analyze`: what the task's nodes show, separately and together —
    written into the task's own file, not a separate record.

    Deliberately not a leaderboard. A benchmark wants one (and gets a computed table
    when the declared metrics have numbers), but the nodes of a task are often parallel
    and independent and still say something jointly — a diagnostic that explains a
    method's failure, two results that contradict each other — and some nodes are
    investigations that produce findings rather than runs. So the evidence is any node's
    runs *or* its written `## Findings`/`## Conclusions`, and the agent is asked for
    per-node findings, cross-node insights and a conclusion, with untested inference
    labelled as such.

    A task's *plan* sections (goal, question, metrics, `## Proposed protocol`,
    `## Decision rule`) are frozen once experiments exist, but its *result* sections are
    not: this is the same split a node already has, where `analyze` keeps writing
    `## Findings` and `## Conclusions` into a node long after its plan is fixed. It sets
    no verdict — a task has no expectation to judge — and re-running it replaces the
    result sections, which is how a partial answer becomes a final one.
    """
    all_nodes = list_nodes(task=task, config=config)
    nodes, left_out = _select_nodes(all_nodes, only)
    runs_by_id, states, _ = _node_bookkeeping(nodes, config=config)
    has_evidence = any(
        runs_by_id[node.id]
        or sections.read_section(node.markdown, "Findings")
        or sections.read_section(node.markdown, "Conclusions")
        or sections.read_section(node.markdown, INVESTIGATION_HEADING)
        for node in nodes
    )
    if not has_evidence:
        raise HirakuError(
            f"Task '{task.id}' has no evidence to analyse yet: no node"
            f"{' of those selected' if left_out else ''} has runs or a written "
            f"Findings, Conclusions or Investigation section."
        )
    board = compare.build_leaderboard(task, nodes, runs_by_id)
    command, timeout, context_paths = apply_overrides(
        action,
        context_id,
        entity=task.id,
        command=command,
        timeout=timeout,
        context_paths=context_paths,
        config=config,
    )
    command = with_repos(command, repo_paths)
    bundle = _resolve_context(config, context_paths, context_bytes)
    state: dict[str, Any] = {
        "task": task.model_dump(mode="json", exclude={"path"}),
        "nodes": [
            {
                "id": node.id,
                "kind": node.kind,
                "parent_id": node.parent_id,
                "state": states[node.id],
                "verdict": node.verdict,
                "expectation": node.expectation,
                "n_runs": len(runs_by_id[node.id]),
                "scope": _excerpt(node.markdown, "Scope", 800),
                "findings": _excerpt(node.markdown, "Findings", 2000),
                "conclusions": _excerpt(node.markdown, "Conclusions", 1500),
                "missing_evidence": _excerpt(node.markdown, "Missing evidence", 500),
                "investigation": _excerpt(node.markdown, INVESTIGATION_HEADING, 2500),
            }
            for node in nodes
        ],
    }
    if left_out:
        state["scope"] = {
            "analysed_only": [node.id for node in nodes],
            "not_part_of_this_analysis": left_out,
        }
    if board.has_numbers:
        state["metric_table"] = board.to_state()
    prompt = build_prompt(
        "analyze_task",
        attach_context(state, bundle),
        instruction=instruction,
        root=config.root,
        repos=repo_paths,
    )
    result = AgentRun(workflow="analyze_task", prompt=prompt, context=bundle)
    if dry_run:
        return result

    result.raw, result.log, result.seconds = invoke(
        config,
        "analyze_task",
        task.id,
        prompt,
        bundle,
        command=command,
        timeout=timeout,
        hooks=hooks,
        unset_env=unset_env,
    )
    result.data = extract_json(result.raw)
    if not apply:
        return result

    live = load_record(task.id, config=config)
    if left_out:
        live.markdown = _write_subset_analysis(
            live.markdown, nodes, left_out, board, result.data
        )
    else:
        markdown = live.markdown
        if board.has_numbers:
            markdown = sections.set_section(
                markdown, "Metric comparison", compare.leaderboard_markdown(board)
            )
        live.markdown = _write_analysis_sections(
            markdown, result.data, _TASK_ANALYSIS_SECTIONS
        )
    live.save()
    result.written.append(live.path)
    return result


def _select_nodes(
    all_nodes: list[Node], only: list[str] | None
) -> tuple[list[Node], list[str]]:
    """The nodes a task-level analysis covers, and the ids it leaves out.

    No selection, or one naming every node, means the whole task (nothing left out).
    An id that is not a node of this task is an error rather than silently dropped: a
    typo would otherwise produce an analysis of fewer nodes than the researcher meant.
    """
    if not only:
        return all_nodes, []
    wanted = [item.strip() for item in only if item and item.strip()]
    known = {node.id for node in all_nodes}
    unknown = sorted(set(wanted) - known)
    if unknown:
        raise HirakuError(
            f"Not nodes of this task: {', '.join(unknown)}. "
            f"Nodes: {', '.join(sorted(known))}."
        )
    chosen = [node for node in all_nodes if node.id in set(wanted)]
    left_out = [node.id for node in all_nodes if node.id not in set(wanted)]
    return chosen, left_out


def _write_subset_analysis(
    markdown: str,
    nodes: list[Node],
    left_out: list[str],
    board: compare.Leaderboard,
    data: dict[str, Any],
) -> str:
    """A subset analysis goes in a section of its own, named for the nodes it covers.

    It must not replace the whole-task `## Findings`/`## Conclusions`: those answer the
    task's question over *all* its nodes, and an analysis of two of them is not that.
    The section says up front which nodes it covers and which it does not; the same
    subset re-run replaces its own section, a different subset gets another.
    """
    ids = [node.id for node in nodes]
    blocks = [
        f"**Analysed only: {', '.join(f'`{i}`' for i in ids)}.** "
        f"{len(left_out)} other node(s) of this task were not part of this analysis "
        f"({', '.join(f'`{i}`' for i in left_out)}), so nothing here is a whole-task "
        f"conclusion."
    ]
    if board.has_numbers:
        table = compare.leaderboard_markdown(board)
        blocks.append(f"### Metric comparison\n\n{table}")
    for key, heading in _TASK_ANALYSIS_SECTIONS:
        if data.get(key):
            blocks.append(f"### {heading}\n\n{str(data[key]).strip()}")
    missing = data.get("missing_evidence")
    if missing:
        blocks.append(f"### Missing evidence\n\n{str(missing).strip()}")
    return sections.set_section(
        markdown, f"Subset analysis: {', '.join(ids)}", "\n\n".join(blocks)
    )


# ----------------------------------------------------------------------------- update

# `hiraku-agent update`: revise an existing task or experiment from an instruction,
# while it is still safe to.
#
# A record's content is only a *plan* until something is built on it. After that it is
# a *fact about what was done*: an experiment with runs has a result recorded against
# its `expectation`, and a task with experiments beneath it has handed its `goal`,
# `question` and `metrics` down to every one of them (`Record.resolve()` inherits
# them). Editing either in place would silently change what existing work was
# measured against. So the rule is deliberately one-directional:
#
# - a **task** (root record, `parent_id is None`) may be revised in full only until
#   another record refers to it — a child under it, or a `related_to` link from
#   anywhere. After that only its `## Decision rule` section may change, and only until
#   some node under it has a verdict: the rule is read at analysis time, not inherited
#   by experiments, and what needs protecting is a verdict reached under it, not the
#   existence of an experiment. The tolerance a rule uses is meant to be calibrated from
#   the baseline's own runs, which cannot exist before the first experiment does;
# - an **experiment** (any other record) may be revised only until it has runs.
#
# Past that point `update` refuses and says what to do instead: draft a new task or
# experiment. The check runs twice — before the agent call, so a refused update costs
# nothing, and again just before writing, because an agent call is minutes long.

TASK_FIELDS = ("goal", "question", "summary")
NODE_FIELDS = ("expectation", "kind")


def is_task(record: Record) -> bool:
    return record.parent_id is None


def check_editable(record: Record, *, config: HirakuConfig) -> str:
    """How `record` may be revised: `"full"`, or `"rule_only"` (a task that experiments
    already refer to but that no verdict has been reached under). Raises `AgentError`
    when it may not be revised at all — see the comment above for the rule and why it is
    one-directional."""
    if is_task(record):
        referrers = sorted(
            other.id
            for other in list_records(config=config)
            if other.id != record.id
            and (other.parent_id == record.id or record.id in other.related_to)
        )
        if not referrers:
            return "full"
        decided = sorted(
            node.id
            for node in list_nodes(task=record, config=config)
            if node.verdict is not None
        )
        if decided:
            shown = ", ".join(decided[:5]) + (" ..." if len(decided) > 5 else "")
            raise AgentError(
                f"Task {record.id!r} can no longer be updated: {len(decided)} "
                f"experiment(s) already have a verdict ({shown}), reached under its "
                f"current goal, metrics and decision rule. Changing any of them now "
                f"would change what that result was judged against. Draft a new task "
                f"instead:\n  hiraku-agent draft -m '...' --task-id <new-id>"
            )
        return "rule_only"
    node = load_node(record.id, config=config)
    runs = node.runs(config=config)
    if runs:
        raise AgentError(
            f"Experiment {record.id!r} can no longer be updated: it already has "
            f"{len(runs)} run(s), so its expectation has results recorded against it. "
            f"Draft a new experiment instead (e.g. `hiraku-agent design --task "
            f"<task-id> -m '...'`, or `hiraku record create <new-id> --parent "
            f"{record.parent_id}`)."
        )
    return "full"


def _state(record: Record, *, mode: str, config: HirakuConfig) -> dict[str, Any]:
    data = record.model_dump(mode="json", exclude={"path"})
    if is_task(record):
        return {"kind": "task", "mode": mode, "record": data}
    root = record
    while root.parent_id is not None:
        root = load_record(root.parent_id, config=config)
    return {
        "kind": "experiment",
        "mode": mode,
        "record": data,
        "task": {
            "id": root.id,
            "goal": getattr(root, "goal", None),
            "question": getattr(root, "question", None),
            "metrics": root.model_dump(mode="json").get("metrics"),
        },
    }


def run_update(
    record_id: str,
    *,
    instruction: str,
    command: list[str] | None = None,
    dry_run: bool = False,
    apply: bool = True,
    context_paths: list[str] | None = None,
    repo_paths: list[str] | None = None,
    context_bytes: int | None = None,
    unset_env: list[str] | None = None,
    hooks: Hooks | None = None,
    timeout: int | None = None,
    action: str | None = None,
    context_id: str | None = None,
    config: HirakuConfig | None = None,
) -> AgentRun:
    """Revise the task or experiment `record_id` from `instruction`.

    Any record id works — task or experiment, resolved from the record itself — which is
    why this takes a bare id rather than `--task`. Only the fields named in
    `TASK_FIELDS`/`NODE_FIELDS`, `metrics` (tasks) and the body can change; identity,
    parentage, branch, commit and entrypoints never do.

    Revising an experiment's `expectation` clears any approval already given
    (`approved_by`/`approved_at`): approval was for the expectation as it stood, and
    carrying it over would let an edit slip past the gate in `hiraku_agent.gates`.
    """
    if not (instruction or "").strip():
        raise AgentError("An instruction is required to update a record.")
    selected = config or HirakuConfig.discover()
    record = load_record(record_id, config=selected)
    mode = check_editable(record, config=selected)
    command, timeout, context_paths = apply_overrides(
        action,
        context_id,
        entity=record.id,
        command=command,
        timeout=timeout,
        context_paths=context_paths,
        config=selected,
    )
    command = with_repos(command, repo_paths)
    bundle = _resolve_context(selected, context_paths, context_bytes)
    prompt = build_prompt(
        "update",
        attach_context(_state(record, mode=mode, config=selected), bundle),
        instruction=instruction,
        root=selected.root,
        repos=repo_paths,
    )
    result = AgentRun(workflow="update", prompt=prompt, context=bundle)
    if dry_run:
        return result

    result.raw, result.log, result.seconds = invoke(
        selected,
        "update",
        record.id,
        prompt,
        bundle,
        command=command,
        timeout=timeout,
        hooks=hooks,
        unset_env=unset_env,
    )
    result.data = extract_json(result.raw)
    if not apply:
        return result

    # Reload and re-check: the call took a while, and runs or children may have
    # appeared.
    live: Record = load_node(record.id, config=selected)
    mode = check_editable(live, config=selected)
    data = result.data
    changed: list[str] = []
    if mode == "rule_only":
        return _apply_rule_only(live, result, data)

    def _text(key: str) -> str | None:
        value = data.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else None

    if is_task(live):
        for key in TASK_FIELDS:
            value = _text(key)
            if value is not None and value != getattr(live, key, None):
                setattr(live, key, value)
                changed.append(key)
        proposed = data.get("metrics")
        if isinstance(proposed, list) and proposed:
            metrics = _sanitize_metrics(data)
            if not metrics:
                raise AgentError(
                    "The agent returned `metrics`, but none had both a name and a "
                    "minimize/maximize direction, so nothing was written."
                )
            current = [
                {"name": m.name, "direction": m.direction} for m in live.metrics or []
            ]
            if metrics != current:
                live.metrics = metrics
                changed.append("metrics")
    else:
        assert isinstance(live, Node)
        node_changed, cleared = _apply_node_fields(live, data)
        changed += node_changed
        if cleared:
            result.data["approval_cleared"] = True
    if is_task(live):
        body = data.get("body")
        if (
            isinstance(body, str)
            and body.strip()
            and body.strip() != live.markdown.strip()
        ):
            live.markdown = body.rstrip() + "\n"
            changed.append("body")

    result.data["changed"] = changed
    if changed:
        live.save()
        result.written.append(live.path)
    return result


def _apply_node_fields(live: Node, data: dict[str, Any]) -> tuple[list[str], bool]:
    """Apply an agent's edit to an experiment: `expectation`, `kind` and the body.

    Returns the names of what changed and whether an approval was cleared. Changing the
    expectation clears any approval already given, because approval was for the
    expectation as it stood. The caller saves.
    """

    def _text(key: str) -> str | None:
        value = data.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else None

    changed: list[str] = []
    cleared = False
    expectation = _text("expectation")
    if expectation is not None and expectation != live.expectation:
        live.expectation = expectation
        changed.append("expectation")
        if live.approved_by is not None or live.approved_at is not None:
            live.approved_by = None
            live.approved_at = None
            cleared = True
    kind = _text("kind")
    if kind is not None and kind != live.kind:
        if kind not in KINDS:
            raise AgentError(
                f"The agent proposed kind {kind!r}; one of {', '.join(KINDS)}."
            )
        live.kind = kind  # type: ignore[assignment]
        changed.append("kind")
    body = data.get("body")
    if isinstance(body, str) and body.strip() and body.strip() != live.markdown.strip():
        live.markdown = body.rstrip() + "\n"
        changed.append("body")
    return changed, cleared


def run_revise(
    task_id: str,
    *,
    instruction: str,
    command: list[str] | None = None,
    dry_run: bool = False,
    apply: bool = True,
    context_paths: list[str] | None = None,
    repo_paths: list[str] | None = None,
    context_bytes: int | None = None,
    unset_env: list[str] | None = None,
    hooks: Hooks | None = None,
    timeout: int | None = None,
    action: str | None = None,
    context_id: str | None = None,
    config: HirakuConfig | None = None,
) -> AgentRun:
    """Revise the experiments `design` or `discover` wrote under a task, in one call.

    The counterpart of `run_design`: that one adds nodes, this one edits the ones that
    exist. The scope is the whole task, so the task's own record is never touched (that
    is `run_update`), and nothing has to be listed. Only experiments that may still be
    revised are shown as editable (no runs yet); the rest are shown for context and
    cannot change. One call sees every node, so a value they share is settled once.
    """
    if not (instruction or "").strip():
        raise AgentError("An instruction is required to revise experiments.")
    selected = config or HirakuConfig.discover()
    task = load_record(task_id, config=selected)
    nodes = list_nodes(task=task, config=selected)
    editable: dict[str, Node] = {}
    frozen: dict[str, str] = {}
    for node in nodes:
        try:
            check_editable(node, config=selected)
            editable[node.id] = node
        except AgentError as exc:
            frozen[node.id] = str(exc).split(" Draft a new", 1)[0]
    if not editable:
        raise AgentError(
            f"Task {task.id!r} has no experiments that can still be revised"
            + (
                ": " + "; ".join(f"{k}: {v}" for k, v in frozen.items())
                if frozen
                else ""
            )
            + "."
        )
    command, timeout, context_paths = apply_overrides(
        action,
        context_id,
        entity=task.id,
        command=command,
        timeout=timeout,
        context_paths=context_paths,
        config=selected,
    )
    command = with_repos(command, repo_paths)
    bundle = _resolve_context(selected, context_paths, context_bytes)
    state = {
        "task": task.model_dump(mode="json", exclude={"path"}),
        "editable": {
            node_id: node.model_dump(mode="json", exclude={"path"})
            for node_id, node in editable.items()
        },
        "not_editable": frozen,
    }
    prompt = build_prompt(
        "revise",
        attach_context(state, bundle),
        instruction=instruction,
        root=selected.root,
        repos=repo_paths,
    )
    result = AgentRun(workflow="revise", prompt=prompt, context=bundle)
    if dry_run:
        return result

    result.raw, result.log, result.seconds = invoke(
        selected,
        "revise",
        task.id,
        prompt,
        bundle,
        command=command,
        timeout=timeout,
        hooks=hooks,
        unset_env=unset_env,
    )
    result.data = extract_json(result.raw)
    edits = _require(result.data, "nodes", dict)
    if not apply:
        return result

    changed: list[str] = []
    cleared: list[str] = []
    skipped: list[str] = []
    for node_id, edit in edits.items():
        if not isinstance(edit, dict):
            continue
        if node_id not in editable:
            reason = frozen.get(node_id, "not an experiment of this task")
            skipped.append(f"{node_id}: {reason}")
            continue
        # The call took a while: runs may have appeared, so check again per node.
        live = load_node(node_id, task=task, config=selected)
        try:
            check_editable(live, config=selected)
        except AgentError as exc:
            skipped.append(f"{node_id}: {str(exc).split(' Draft a new', 1)[0]}")
            continue
        fields, was_cleared = _apply_node_fields(live, edit)
        if fields:
            live.save()
            result.written.append(live.path)
            changed.append(f"{node_id} ({', '.join(fields)})")
        if was_cleared:
            cleared.append(node_id)
    result.data["changed"] = changed
    result.data["skipped"] = skipped
    result.data["approvals_cleared"] = cleared
    return result


def _apply_rule_only(live: Record, result: AgentRun, data: dict[str, Any]) -> AgentRun:
    """Take only the `## Decision rule` section out of the agent's reply.

    Everything else the agent returned is ignored and reported, rather than rejected:
    the rest of the task is already inherited by experiments, so it cannot change, but a
    good revision of the rule should not be thrown away because the agent also touched
    something else.
    """
    body = data.get("body")
    new_body = body if isinstance(body, str) else ""
    rule = sections.read_section(new_body, DECISION_RULE_HEADING)
    if not rule:
        raise AgentError(
            "This task already has experiments, so only its `## Decision rule` section "
            "can change, but the agent's reply had none. Nothing was written."
        )
    ignored = [
        key
        for key in TASK_FIELDS
        if isinstance(data.get(key), str)
        and data[key].strip()
        and data[key].strip() != getattr(live, key, None)
    ]
    current_metrics = [
        {"name": m.name, "direction": m.direction} for m in live.metrics or []
    ]
    if data.get("metrics") and _sanitize_metrics(data) != current_metrics:
        ignored.append("metrics")
    blank = sections.set_section(new_body, DECISION_RULE_HEADING, "")
    if (
        blank.strip()
        != sections.set_section(live.markdown, DECISION_RULE_HEADING, "").strip()
    ):
        ignored.append("body outside ## Decision rule")
    result.data["ignored"] = ignored
    changed: list[str] = []
    if rule != sections.read_section(live.markdown, DECISION_RULE_HEADING):
        live.markdown = sections.set_section(live.markdown, DECISION_RULE_HEADING, rule)
        changed.append("decision_rule")
    result.data["changed"] = changed
    if changed:
        live.save()
        result.written.append(live.path)
    return result
