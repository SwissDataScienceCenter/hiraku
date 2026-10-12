"""`discover`: search for candidate hypotheses and investigate what exists."""

from __future__ import annotations

import datetime as _dt
from typing import Any

from hiraku.config import HirakuConfig
from hiraku.errors import HirakuError
from hiraku.record import load_record

from .. import sections
from ..engine import (
    UNVERIFIED_FILES_NOTE,
    UNVERIFIED_NOTE,
    AgentRun,
    Hooks,
    agent_command,
    files_readable,
    web_allowed,
    web_was_denied,
)
from ..lock import writes
from ..node import (
    Node,
    create_node,
    list_nodes,
    load_node,
    task_of,
)
from ..replies import DiscoverReply
from ._common import (
    AgentCall,
    _create_nodes,
    _existing_ids,
    _NewNode,
    _origin,
    _require,
    _write_sections,
    task_state,
)

# ----------------------------------------------------------------------------- discover


@writes("discover")
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
    interactive: bool = False,
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
    call = AgentCall.resolve(
        selected,
        interactive=interactive,
        workflow="discover",
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
    state = task_state(
        task, nodes, config=selected, focus=None if focus_id == task.id else focus_id
    )
    state["focus"] = {
        "id": focus_id,
        "kind": "task" if focus_id == task.id else "experiment",
    }
    state["mode"] = "findings_only" if findings_only else "propose_and_investigate"
    result = call.run(
        "discover",
        focus_id,
        state,
        instruction=instruction,
        dry_run=dry_run,
        reply=DiscoverReply,
    )
    if dry_run:
        return result
    injected = result.data.get("injected_instructions_found")
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
    resolved_command = call.command or agent_command()
    unverified = not web_allowed(resolved_command) or web_was_denied(result.raw)
    result.data["citations_unverified"] = unverified

    # A source that tried to instruct the agent is the one path from the web into a
    # record that `implement` (which can run shell commands) will later read. Every
    # record this call writes says so, and its proposals are pinned to `guided` mode
    # so a person sees the warning at the approval gate even under an `auto` loop.
    warning = _injection_warning(injected)
    result.data["injection_warning"] = bool(warning)

    def warn(node: Node) -> None:
        if warning:
            node.markdown = sections.set_section(
                node.markdown, INJECTION_HEADING, warning
            )

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
            **({"mode": "guided"} if warning else {}),
            config=selected,
        )
        warn(node)
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
        warn(node)
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
    # `DiscoverReply` has already checked that every item is an object with an id.
    for item in proposals:
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
        files_unverified=not files_readable(call.command, call.bundle),
    )
    if entry and warning:
        entry = f"{entry}\n\n{warning}"
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
INJECTION_HEADING = "Injection warning"


def _injection_warning(injected: Any) -> str:
    """The `## Injection warning` text for what the agent quoted, or `""` for none."""
    if not injected:
        return ""
    quotes = injected if isinstance(injected, list) else [injected]
    quoted = "\n".join(f"> {' '.join(str(q).split())[:500]}" for q in quotes if q)
    if not quoted:
        return ""
    return (
        "**[WARNING: a source read while writing this record contained text that tried "
        "to instruct the agent. Check the sections above against their cited sources "
        "before approving or implementing anything from them.]**\n\n" + quoted
    )


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
