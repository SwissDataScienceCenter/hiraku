"""`design`: propose a tree of experiments for a task."""

from __future__ import annotations

from typing import Any

from hiraku.config import HirakuConfig
from hiraku.record import load_record

from .. import sections
from ..engine import AgentRun, Hooks
from ..lock import writes
from ..node import (
    Node,
    create_node,
    list_nodes,
)
from ..replies import DesignReply
from ._common import (
    AgentCall,
    _create_nodes,
    _NewNode,
    _origin,
    _write_sections,
    task_state,
)

# ------------------------------------------------------------------------------- design


@writes("design")
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
    call = AgentCall.resolve(
        selected,
        entity=task.id,
        action=action,
        context_id=context_id,
        command=command,
        timeout=timeout,
        context_paths=context_paths,
        context_bytes=context_bytes,
        repo_paths=repo_paths,
        hooks=hooks,
        unset_env=unset_env,
    )
    result = call.run(
        "design",
        task.id,
        task_state(task, nodes, config=selected),
        instruction=instruction,
        dry_run=dry_run,
        reply=DesignReply,
    )
    if dry_run:
        return result
    proposed = result.data["experiments"]
    if not call.bundle.files and not repo_paths:
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

    # `DesignReply` has already checked that every item is an object with an id.
    created = _create_nodes(
        task,
        [
            _NewNode(
                str(item["id"]),
                str(item["parent"]) if item.get("parent") else None,
                lambda item=item: build(item),
            )
            for item in proposed
        ],
        config=selected,
    )
    order = {str(item["id"]): index for index, item in enumerate(proposed)}
    for node in sorted(created, key=lambda node: order[node.id]):
        result.written.append(node.path)
    return result


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
