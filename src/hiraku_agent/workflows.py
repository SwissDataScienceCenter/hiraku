"""design/discover/triage/analyze: the LLM-driven half of the research loop.

`schedule.suggest_next` says *that* one of these is next; this module is what actually
runs one — building the state an agent sees, calling it through `engine.invoke`, and
writing what it returns as ordinary `hiraku_agent.Node` records. See `engine`'s module
docstring for the call mechanics, and `prompts/_rules.md` for the contract every
workflow shares.

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
  a `Node` with `abandoned=True` and no commit — `docs/state.md`'s `abandoned` state
  already means exactly "considered, not pursued", so an idea nobody built is the same
  fact as a node somebody dropped; both are the novelty-check memory the six-condition
  test in `prompts/_rules.md` asks for.

Every node this module writes is a plain `hiraku` experiment underneath — `hiraku
experiment show` reads it unmodified; only the extra fields carry meaning `hiraku`
itself doesn't know about (see `node.py`'s module docstring).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from hiraku.config import HirakuConfig
from hiraku.data import Task, load_task
from hiraku.errors import HirakuError

from . import sections
from .engine import (
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
    config: HirakuConfig | None = None,
) -> AgentRun:
    """Propose a tree of experiments for a task. Writes proposed `Node`s (no commit)."""
    selected = config or HirakuConfig.discover()
    task = load_task(task_id, config=selected)
    nodes = list_nodes(task=task, config=selected)
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
    config: HirakuConfig | None = None,
) -> AgentRun:
    """Classify why a node's result missed its expectation, and decide whether to
    follow up.

    Expects the node already has a verdict-worthy situation (usually
    `awaiting_analysis` or `concluded_untriaged` — see `docs/state.md`); it does not
    check the derived state itself, since a researcher may reasonably triage a node
    the scheduler hasn't gotten to yet.
    """
    selected = config or HirakuConfig.discover()
    node = load_node(node_id, task=task_id, config=selected)
    task = load_task(node.task_id, config=selected)
    nodes = list_nodes(task=task, config=selected)
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
    config: HirakuConfig | None = None,
) -> AgentRun:
    """Judge a node's runs against its expectation and write its findings."""
    selected = config or HirakuConfig.discover()
    node = load_node(node_id, task=task_id, config=selected)
    if not node.runs(config=selected):
        raise HirakuError(f"'{node.id}' has no runs to analyse.")
    task = load_task(node.task_id, config=selected)
    nodes = list_nodes(task=task, config=selected)
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
