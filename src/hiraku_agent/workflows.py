"""design/discover/implement/triage/analyze: the LLM-driven half of the research loop.

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
  has exactly one kind of record for an attempt: `Experiment` (`Node`, here) — a
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

Every node this module writes is a plain `hiraku` experiment underneath — `hiraku
experiment show` reads it unmodified; only the extra fields carry meaning `hiraku`
itself doesn't know about (see `node.py`'s module docstring).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from contextlib import suppress
from typing import Any

from hiraku.config import HirakuConfig
from hiraku.data import Task, list_tasks, load_task
from hiraku.errors import HirakuError
from hiraku.git import GitRepository
from hiraku.validation import validate_identifier

from . import sections
from .actions import apply_overrides
from .engine import (
    DEFAULT_IMPLEMENT_AGENT_CMD,
    IMPLEMENT_AGENT_CMD_ENV_VAR,
    UNVERIFIED_NOTE,
    AgentRun,
    Bundle,
    Hooks,
    agent_command,
    attach_context,
    build_prompt,
    extract_json,
    gather_context,
    invoke,
    web_allowed,
    web_was_denied,
)
from .errors import AgentError
from .gates import is_approved
from .node import ORIGINS, Node, create_node, list_nodes, load_node
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
    """`run.model_dump()`, tolerant of `hiraku.data.Run`'s normalized fields.

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
        if node.parent:
            children_of[node.parent].append(node.id)
    runs_by_id = {node.id: node.runs(config=config) for node in nodes}
    states = {
        node.id: node_state(
            node, runs_by_id[node.id], has_children=bool(children_of[node.id])
        )
        for node in nodes
    }
    return runs_by_id, states, children_of


def task_state(
    task: Task,
    nodes: list[Node],
    *,
    config: HirakuConfig,
    focus: str | None = None,
) -> dict[str, Any]:
    """The state an agent sees: the task's own record, plus every node scoped to it.

    Scoped to one task on purpose, the way the old `hiraku_auto`'s `slim_state` scoped
    to one goal — it keeps the prompt small and stops the agent reasoning across
    incomparable protocols. `hiraku_agent` has no separate goal layer to scope by (a
    `Task` already carries `goal`/`question`/`metrics` — see `node.py`'s module
    docstring and `docs/state.md`), so scoping by task is the whole of it.
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
    id-collision and novelty checks `prompts/draft.md` asks it to make."""
    existing = list_tasks(config=config)
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


def _sanitize_metrics(data: dict[str, Any]) -> list[dict[str, str]]:
    """Keep only agent-proposed metrics shaped the way `hiraku.data.Metric` requires: a
    non-empty `name` and a `direction` its `Literal["minimize", "maximize"]` will
    accept.

    A metric missing either is dropped rather than guessed. `Metric.direction` has no
    room for an `**[UNSET: ...]**` marker the way a plain string field does — passing
    one through would fail `Task.create`'s validation outright — so the honest move for
    an ungroundable direction is to leave that metric out, not invent one that could
    silently point every later comparison the wrong way.
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
        direction = str(item.get("direction") or "").strip()
        if not name or direction not in _METRIC_DIRECTIONS or name in seen:
            continue
        metrics.append({"name": name, "direction": direction})
        seen.add(name)
    return metrics


def _create_task(
    task_id: str,
    data: dict[str, Any],
    *,
    branch: str | None,
    activate: bool,
    config: HirakuConfig,
) -> Task:
    """Create a `Task` from a `draft` reply, the same way `hiraku task create` does by
    hand: resolve and validate the branch name, persist the record, then bind the git
    branch — rolling the record back if the branch step fails, so the two never diverge.
    """
    fields: dict[str, Any] = {
        key: str(data[key]) for key in ("goal", "question", "summary") if data.get(key)
    }
    metrics = _sanitize_metrics(data)
    if metrics:
        fields["metrics"] = metrics
    body = str(data.get("body") or "")
    selected_branch = (
        branch if branch is not None else config.task_branch.format(task_id=task_id)
    )
    repository = GitRepository(config)
    repository.validate_branch(selected_branch)
    created = Task.create(
        task_id,
        markdown=body,
        activate=False,
        config=config,
        branch=selected_branch,
        **fields,
    )
    created_leaf = created.path.parent
    try:
        repository.create_task_branch(task_id, branch=selected_branch)
    except Exception:
        created.delete(config=config)
        _remove_empty_task_leaf(created_leaf, config.tasks_dir / task_id)
        raise
    if activate:
        config.set_active_task(created)
    return created


def run_draft(
    instruction: str,
    *,
    task_id: str | None = None,
    command: list[str] | None = None,
    dry_run: bool = False,
    apply: bool = True,
    context_paths: list[str] | None = None,
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
    """Turn free-text instruction into a new `Task` — the same effect as running `hiraku
    task create` by hand.

    Unlike `design`/`discover`/`triage`/`analyze`, there is no task to load first: this
    workflow creates one. A `task_id` the caller already knows (`--task-id`) is
    validated before the agent is even called, so a bad id fails fast rather than after
    a paid call; an id the agent proposes instead is validated once the reply is in,
    exactly as `hiraku task create` validates whatever id it is given.

    `action`, if given, needs `task_id` too — an `Action` (`hiraku.agent.Action`) is
    scoped to a task id (`entity`), and there is no task id to scope it to until this
    call has already picked one (see `actions.py`).
    """
    if not (instruction or "").strip():
        raise AgentError("An instruction is required to draft a task.")
    selected = config or HirakuConfig.discover()
    if task_id is not None:
        validate_identifier(task_id, label="task ID")
    command, timeout, context_paths = apply_overrides(
        action,
        context_id,
        entity=task_id,
        command=command,
        timeout=timeout,
        context_paths=context_paths,
        config=selected,
    )
    bundle = _resolve_context(selected, context_paths, context_bytes)
    prompt = build_prompt(
        "draft",
        attach_context(draft_state(selected, task_id=task_id), bundle),
        instruction=instruction,
        root=selected.root,
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
    task = load_task(task_id, config=selected)
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
        "design",
        attach_context(task_state(task, nodes, config=selected), bundle),
        instruction=instruction,
        root=selected.root,
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
    if not apply:
        return result

    for item in proposed:
        if not isinstance(item, dict) or not item.get("id"):
            continue
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
        node.save()
        result.written.append(node.path)
    return result


# ----------------------------------------------------------------------------- discover


def run_discover(
    task_id: str,
    *,
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
    """Search for candidate hypotheses.

    Writes accepted ideas as proposed `Node`s, declined ones as `abandoned` `Node`s —
    see this module's docstring for why both are `Node`s rather than a separate record
    type.
    """
    selected = config or HirakuConfig.discover()
    task = load_task(task_id, config=selected)
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
        "discover",
        attach_context(task_state(task, nodes, config=selected), bundle),
        instruction=instruction,
        root=selected.root,
    )
    result = AgentRun(workflow="discover", prompt=prompt, context=bundle)
    if dry_run:
        return result

    result.raw, result.log, result.seconds = invoke(
        selected,
        "discover",
        task_id,
        prompt,
        bundle,
        command=command,
        timeout=timeout,
        hooks=hooks,
        unset_env=unset_env,
    )
    result.data = extract_json(result.raw)
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
    existing_ids = {node.id for node in nodes}
    skipped: list[str] = []

    for item in proposals:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        node_id = str(item["id"])
        if node_id in existing_ids:
            skipped.append(node_id)
            continue
        node = create_node(
            node_id,
            task=task,
            parent=(
                str(item["proposed_parent"]) if item.get("proposed_parent") else None
            ),
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
        result.written.append(node.path)
        existing_ids.add(node_id)

    # An idea considered and rejected is a record too — otherwise the reasoning lives in
    # terminal scrollback, and next month's discover proposes the same thing again.
    declined: list[str] = []
    for item in result.data.get("declined") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        node_id = str(item["id"])
        if node_id in existing_ids:
            skipped.append(node_id)
            continue
        node = create_node(
            node_id,
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
        result.written.append(node.path)
        existing_ids.add(node_id)
        declined.append(node_id)

    result.data["declined_written"] = declined
    result.data["skipped_existing"] = skipped
    return result


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
            f"implement. Use `hiraku experiment update` directly if you meant to "
            f"rebind it."
        )
    if not is_approved(node):
        raise AgentError(
            f"'{node.id}' is not approved yet. Run `hiraku-agent approve {node.id} "
            f"--task {node.task_id} --by <you>` first — in guided mode that approval "
            f"is what authorizes this workflow to write real code."
        )

    task = load_task(node.task_id, config=selected)
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
    result.data = extract_json(result.raw)
    data = result.data
    if data.get("blocked") or not apply:
        return result

    raw_commit = _require(data, "commit", str)
    repository = GitRepository(selected)
    resolved_commit = repository.resolve(raw_commit)

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
    task = load_task(node.task_id, config=selected)
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
        "triage",
        attach_context(task_state(task, nodes, config=selected, focus=node.id), bundle),
        instruction=instruction,
        root=selected.root,
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
    failure_class = str(data.get("failure_class") or "")
    if failure_class in {"B", "C"} and verdict in {"refutes", "inconclusive"}:
        live.verdict = verdict

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

    # A completed triage call always records its decision, win or lose — this is
    # what lets `state.node_state` tell "never triaged" from "triaged, decided not
    # to go further" once there are no children to show for it either way.
    live.triaged = True
    live.markdown = markdown
    live.save()
    result.written.append(live.path)

    if data.get("proceed"):
        for child in data.get("children") or []:
            if not isinstance(child, dict) or not child.get("id"):
                continue
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
            result.written.append(child_node.path)
    return result


# ------------------------------------------------------------------------------ analyze


def run_analyze(
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
    """Judge a node's runs against its expectation and write its findings."""
    selected = config or HirakuConfig.discover()
    node = load_node(node_id, task=task_id, config=selected)
    if not node.runs(config=selected):
        raise HirakuError(f"'{node.id}' has no runs to analyse.")
    task = load_task(node.task_id, config=selected)
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
        "analyze",
        attach_context(task_state(task, nodes, config=selected, focus=node.id), bundle),
        instruction=instruction,
        root=selected.root,
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
    for key, heading in (
        ("findings", "Findings"),
        ("conclusions", "Conclusions"),
        ("next_steps", "Decisions and next steps"),
    ):
        if result.data.get(key):
            markdown = sections.set_section(markdown, heading, str(result.data[key]))
    verdict = result.data.get("verdict")
    if verdict in {"supports", "refutes", "inconclusive"}:
        live.verdict = verdict
        # A fresh verdict needs a fresh go/no-go decision — don't let a `triaged`
        # flag earned by a *previous* verdict make this node look already decided.
        live.triaged = False
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
