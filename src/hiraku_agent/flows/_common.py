"""Shared plumbing for every workflow: the state an agent sees, one agent call,
all-or-nothing node creation, and small reply helpers."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Any

from hiraku.config import HirakuConfig
from hiraku.record import Record, list_records
from hiraku.validation import validate_identifier

from .. import sections
from ..actions import apply_overrides
from ..engine import (
    AgentRun,
    Bundle,
    Hooks,
    annotate_log,
    attach_context,
    build_prompt,
    extract_json,
    gather_context,
    invoke,
    with_repos,
)
from ..errors import AgentError
from ..gates import is_approved
from ..node import (
    ORIGINS,
    Node,
    list_nodes,
)
from ..replies import Reply
from ..state import node_bookkeeping


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


# Kept under its old private name: `loop` and older callers import it from here.
_node_bookkeeping = node_bookkeeping


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

    Bounded: once the whole tree would exceed `STATE_BUDGET_CHARS`, nodes that are
    `completed` and not on the focus node's own line (itself, its ancestors, its
    children) are cut down to their facts and the head of their conclusions, and every
    `## Investigation` keeps only its latest entries. What was cut is listed under
    `compacted`, so the agent knows to open the record file if it needs the rest.
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

    state: dict[str, Any] = {
        "task": task.model_dump(mode="json", exclude={"path"}),
        "nodes": serialized,
        "focus_node": focus,
    }
    if _size(state) > STATE_BUDGET_CHARS:
        _compact(state, nodes, children_of, focus)
    return state


# Roughly 15k tokens of tree. Below it the state is passed whole, exactly as before;
# above it `_compact` trims what an agent is least likely to need.
STATE_BUDGET_CHARS = 60_000
_KEPT_INVESTIGATION_ENTRIES = 3
_COMPACT_SECTIONS = (("Title", 200), ("Why declined", 600), ("Conclusions", 1200))


def _size(state: dict[str, Any]) -> int:
    return len(json.dumps(state, default=str))


def _compact(
    state: dict[str, Any],
    nodes: list[Node],
    children_of: dict[str, list[str]],
    focus: str | None,
) -> None:
    by_id = {node.id: node for node in nodes}
    keep: set[str] = set()
    if focus in by_id:
        keep |= {focus, *children_of.get(focus, [])}
        parent = by_id[focus].parent_id
        while parent in by_id:
            keep.add(parent)
            parent = by_id[parent].parent_id
    compacted: list[str] = []
    for node_id, payload in state["nodes"].items():
        node = by_id[node_id]
        if node_id not in keep and payload["state"] == "completed":
            state["nodes"][node_id] = {
                key: payload.get(key)
                for key in (
                    "id",
                    "parent_id",
                    "kind",
                    "expectation",
                    "verdict",
                    "abandoned",
                    "state",
                    "children",
                )
            } | {
                "n_runs": len(payload.get("runs") or []),
                "sections": {
                    heading: sections.read_section(node.markdown, heading)[:limit]
                    for heading, limit in _COMPACT_SECTIONS
                    if sections.read_section(node.markdown, heading)
                },
            }
            compacted.append(node_id)
        elif payload.get("markdown"):
            payload["markdown"] = _latest_investigations(payload["markdown"])
    state["compacted"] = {
        "nodes": compacted,
        "why": (
            f"the full tree exceeds {STATE_BUDGET_CHARS} characters; these completed "
            "nodes are summarised and older `## Investigation` entries are omitted. "
            "Read the record file under hiraku/records/ if you need the rest."
        ),
    }


def _latest_investigations(markdown: str) -> str:
    """`markdown` with only the latest `## Investigation` entries (each a `###`)."""
    body = sections.read_section(markdown, "Investigation")
    # The section starts with its first `###` entry, so anchor every entry on a newline.
    preamble, *entries = ("\n" + body).split("\n### ")
    if len(entries) <= _KEPT_INVESTIGATION_ENTRIES:
        return markdown
    omitted = len(entries) - _KEPT_INVESTIGATION_ENTRIES
    kept = "\n\n".join(
        f"### {entry.strip()}" for entry in entries[-_KEPT_INVESTIGATION_ENTRIES:]
    )
    note = f"_{omitted} earlier entr{'y' if omitted == 1 else 'ies'} omitted._"
    parts = [part for part in (preamble.strip(), note, kept) if part]
    return sections.set_section(markdown, "Investigation", "\n\n".join(parts))


@dataclass
class AgentCall:
    """One agent call's resolved inputs, and the two steps every workflow takes with
    them: build the prompt, then call the agent and parse its reply.

    Each workflow used to repeat this pipeline inline (Action overrides, `--repo`,
    context files, prompt, `invoke`, `extract_json`); it lives here once, so a change to
    how an agent is called changes every workflow the same way.
    """

    config: HirakuConfig
    command: list[str] | None
    timeout: int | None
    bundle: Bundle
    repo_paths: list[str] | None = None
    hooks: Hooks | None = None
    unset_env: list[str] | None = None

    @classmethod
    def resolve(
        cls,
        config: HirakuConfig,
        *,
        entity: str | None,
        action: str | None = None,
        context_id: str | None = None,
        command: list[str] | None = None,
        timeout: int | None = None,
        context_paths: list[str] | None = None,
        context_bytes: int | None = None,
        repo_paths: list[str] | None = None,
        hooks: Hooks | None = None,
        unset_env: list[str] | None = None,
    ) -> AgentCall:
        """Apply a saved `Action` (scoped to `entity`), `--repo` and the context files,
        in that order; explicit arguments win over the Action's saved values."""
        command, timeout, context_paths = apply_overrides(
            action,
            context_id,
            entity=entity,
            command=command,
            timeout=timeout,
            context_paths=context_paths,
            config=config,
        )
        return cls(
            config=config,
            command=with_repos(command, repo_paths),
            timeout=timeout,
            bundle=gather_context(config.root, context_paths, max_bytes=context_bytes),
            repo_paths=repo_paths,
            hooks=hooks,
            unset_env=unset_env,
        )

    def prepare(
        self, workflow: str, state: dict[str, Any], *, instruction: str = ""
    ) -> AgentRun:
        """The run as `--dry-run` shows it: the prompt, nothing called yet."""
        prompt = build_prompt(
            workflow,
            attach_context(state, self.bundle),
            instruction=instruction,
            root=self.config.root,
            repos=self.repo_paths,
        )
        return AgentRun(workflow=workflow, prompt=prompt, context=self.bundle)

    def ask(
        self,
        result: AgentRun,
        target: str,
        *,
        reply: type[Reply] | None = None,
        cwd: Path | None = None,
    ) -> AgentRun:
        """Call the agent with `result.prompt` and parse its JSON reply into
        `result.data`, checked against `reply` when given (see `replies.py`).

        An unusable or `blocked` reply is noted in the call's `meta.json`, so that
        `hiraku-agent next` can say the step it suggests just failed (`last_attempt`).
        """
        result.raw, result.log, result.seconds = invoke(
            self.config,
            result.workflow,
            target,
            result.prompt,
            self.bundle,
            command=self.command,
            timeout=self.timeout,
            hooks=self.hooks,
            unset_env=self.unset_env,
            cwd=cwd,
        )
        try:
            result.data = extract_json(result.raw)
            if reply is not None:
                reply.check(result.data, workflow=result.workflow)
        except AgentError as exc:
            annotate_log(result.log, error=str(exc))
            raise
        if result.data.get("blocked"):
            annotate_log(result.log, blocked=str(result.data["blocked"]))
        return result

    def run(
        self,
        workflow: str,
        target: str,
        state: dict[str, Any],
        *,
        instruction: str = "",
        dry_run: bool = False,
        reply: type[Reply] | None = None,
    ) -> AgentRun:
        """`prepare`, then `ask` unless `dry_run`."""
        result = self.prepare(workflow, state, instruction=instruction)
        if dry_run:
            return result
        return self.ask(result, target, reply=reply)


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
