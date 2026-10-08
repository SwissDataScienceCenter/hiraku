"""`update` and `design --revise`: revise records while it is still safe to."""

from __future__ import annotations

import datetime as _dt
from typing import Any

from hiraku.config import HirakuConfig
from hiraku.errors import HirakuError
from hiraku.record import Record, list_records, load_record

from .. import sections
from ..engine import AgentRun, Hooks
from ..errors import AgentError
from ..lock import writes
from ..node import (
    KINDS,
    Node,
    list_nodes,
    load_node,
    task_of,
)
from ..replies import ReviseReply
from ..state import node_bookkeeping
from ._common import AgentCall
from .draft import DECISION_RULE_HEADING, _sanitize_metrics

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


def check_editable(record: Record, *, config: HirakuConfig, force: bool = False) -> str:
    """How `record` may be revised: `"full"`, or `"rule_only"` (a task that experiments
    already refer to but that no verdict has been reached under). Raises `AgentError`
    when it may not be revised at all — see the comment above for the rule and why it is
    one-directional.

    `force` (tasks only) allows a full revision of a task whose experiments are all
    still plans — no runs, no verdict, no launch in flight — on the understanding that
    the caller then brings every experiment back in line (`run_update` does: it sends
    them back to `proposed` and re-syncs them with `run_revise`)."""
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
        if force:
            _check_forceable(record, referrers, config=config)
            return "full"
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


def _check_forceable(
    task: Record, referrers: list[str], *, config: HirakuConfig
) -> None:
    """Refuse `update --force` on a task whose experiments are no longer only plans."""
    nodes = list_nodes(task=task, config=config)
    inside = {node.id for node in nodes}
    outside = [item for item in referrers if item not in inside]
    if outside:
        raise AgentError(
            f"Task {task.id!r} is referred to from outside its own tree "
            f"({', '.join(outside)}), so --force cannot bring everything that depends "
            f"on it back in line. Draft a new task instead."
        )
    runs_by_id, states, _ = node_bookkeeping(nodes, config=config)
    measured = sorted(
        node_id
        for node_id, runs in runs_by_id.items()
        if runs or states.get(node_id) == "running"
    )
    if measured:
        shown = ", ".join(measured[:5]) + (" ..." if len(measured) > 5 else "")
        raise AgentError(
            f"Task {task.id!r} cannot be updated, even with --force: {len(measured)} "
            f"experiment(s) already have runs or a launch in flight ({shown}), "
            f"measured under its current protocol. Draft a new task instead:\n"
            f"  hiraku-agent draft -m '...' --task-id <new-id>"
        )


SUPERSEDED_HEADING = "Superseded implementation"


def _resync_experiments(
    task: Record,
    *,
    instruction: str,
    changed: list[str],
    revise_kwargs: dict[str, Any],
    config: HirakuConfig,
) -> dict[str, Any]:
    """After `update --force` changed a task: send every experiment back to
    `proposed` (unbind its commit, clear its approval) and have `run_revise` bring
    them in line with the updated task."""
    today = _dt.date.today().isoformat()
    unbound: list[str] = []
    cleared: list[str] = []
    for node in list_nodes(task=task, config=config):
        if node.abandoned:
            continue
        touched = False
        if node.commit:
            node.markdown = sections.append_section(
                node.markdown,
                SUPERSEDED_HEADING,
                f"- {today}: commit `{node.commit}` unbound because task `{task.id}` "
                f"was updated ({', '.join(changed)}). Re-implement once re-approved; "
                f"the old code is still in Git history.",
            )
            node.commit = None
            unbound.append(node.id)
            touched = True
        if node.approved_by is not None or node.approved_at is not None:
            node.approved_by = None
            node.approved_at = None
            cleared.append(node.id)
            touched = True
        if touched:
            node.save()
    sync_instruction = (
        f"The task was just updated ({', '.join(changed)}) from this instruction: "
        f"{instruction.strip()}\n\nBring every experiment in line with the updated "
        "task: its goal, question, metrics, protocol and decision rule. Change an "
        "experiment only where the update affects it; keep each one's idea."
    )
    try:
        revised = run_revise(
            task.id, instruction=sync_instruction, config=config, **revise_kwargs
        )
    except HirakuError as exc:
        raise AgentError(
            f"Task {task.id!r} was updated and its experiments were sent back to "
            f"proposed, but re-syncing them failed: {exc}. Re-run:\n"
            f"  hiraku-agent design --task {task.id} --revise -m "
            f"'bring the experiments in line with the updated task'"
        ) from exc
    return {
        "unbound": unbound,
        "approvals_cleared": sorted(
            set(cleared) | set(revised.data.get("approvals_cleared") or [])
        ),
        "synced": revised.data.get("changed") or [],
        "sync_notes": revised.data.get("notes"),
        "written": list(revised.written),
    }


def _state(record: Record, *, mode: str, config: HirakuConfig) -> dict[str, Any]:
    data = record.model_dump(mode="json", exclude={"path"})
    if is_task(record):
        return {"kind": "task", "mode": mode, "record": data}
    root = task_of(record, config=config)
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


@writes("update")
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
    force: bool = False,
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

    `force` (tasks only) revises a task whose experiments are still plans — none has
    runs, a verdict or a launch in flight — and then brings them in line: each goes
    back to `proposed` (its commit, if any, is unbound and noted under
    `## Superseded implementation`; its approval is cleared) and `run_revise` updates
    them all against the new task in one call.
    """
    if not (instruction or "").strip():
        raise AgentError("An instruction is required to update a record.")
    selected = config or HirakuConfig.discover()
    record = load_record(record_id, config=selected)
    if force and not is_task(record):
        raise AgentError("--force applies to a task; an experiment needs no --force.")
    mode = check_editable(record, config=selected, force=force)
    call = AgentCall.resolve(
        selected,
        entity=record.id,
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
        "update",
        record.id,
        _state(record, mode=mode, config=selected),
        instruction=instruction,
        dry_run=dry_run,
    )
    if dry_run or not apply:
        return result

    # Reload and re-check: the call took a while, and runs or children may have
    # appeared.
    live: Record = load_node(record.id, config=selected)
    mode = check_editable(live, config=selected, force=force)
    data = result.data
    changed: list[str] = []
    if mode == "rule_only":
        return _apply_rule_only(live, result, data)

    if is_task(live):
        for key in TASK_FIELDS:
            value = _text(data, key)
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
    if force and changed and list_nodes(task=live, config=selected):
        synced = _resync_experiments(
            live,
            instruction=instruction,
            changed=changed,
            revise_kwargs={
                "command": command,
                "context_paths": context_paths,
                "repo_paths": repo_paths,
                "context_bytes": context_bytes,
                "unset_env": unset_env,
                "hooks": hooks,
                "timeout": timeout,
            },
            config=selected,
        )
        result.written += synced.pop("written")
        sync_notes = synced.pop("sync_notes")
        result.data.update(synced)
        if sync_notes:
            result.data["notes"] = "\n".join(
                part for part in (result.data.get("notes"), sync_notes) if part
            )
    return result


def _text(data: dict[str, Any], key: str) -> str | None:
    """`data[key]` stripped, or None when it is missing, not a string, or blank."""
    value = data.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _apply_node_fields(live: Node, data: dict[str, Any]) -> tuple[list[str], bool]:
    """Apply an agent's edit to an experiment: `expectation`, `kind` and the body.

    Returns the names of what changed and whether an approval was cleared. Changing the
    expectation clears any approval already given, because approval was for the
    expectation as it stood. The caller saves.
    """
    changed: list[str] = []
    cleared = False
    expectation = _text(data, "expectation")
    if expectation is not None and expectation != live.expectation:
        live.expectation = expectation
        changed.append("expectation")
        if live.approved_by is not None or live.approved_at is not None:
            live.approved_by = None
            live.approved_at = None
            cleared = True
    kind = _text(data, "kind")
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


@writes("revise")
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
    state = {
        "task": task.model_dump(mode="json", exclude={"path"}),
        "editable": {
            node_id: node.model_dump(mode="json", exclude={"path"})
            for node_id, node in editable.items()
        },
        "not_editable": frozen,
    }
    result = call.run(
        "revise",
        task.id,
        state,
        instruction=instruction,
        dry_run=dry_run,
        reply=ReviseReply,
    )
    if dry_run or not apply:
        return result
    edits = result.data["nodes"]

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
    force_hint = (
        f"the task already has experiments, so only its Decision rule may change "
        f"without --force. `hiraku-agent update {live.id} --force -m '...'` updates "
        f"the whole task and sends its experiments back to proposed, re-synced with "
        f"it (only while none has runs)."
    )
    if not rule:
        raise AgentError(
            "This task already has experiments, so only its `## Decision rule` section "
            "can change, but the agent's reply had none. Nothing was written."
            + (f" To change {', '.join(ignored)}: {force_hint}" if ignored else "")
        )
    result.data["ignored"] = ignored
    if ignored:
        result.data["hint"] = force_hint
    changed: list[str] = []
    if rule != sections.read_section(live.markdown, DECISION_RULE_HEADING):
        live.markdown = sections.set_section(live.markdown, DECISION_RULE_HEADING, rule)
        changed.append("decision_rule")
    result.data["changed"] = changed
    if changed:
        live.save()
        result.written.append(live.path)
    return result
