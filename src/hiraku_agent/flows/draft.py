"""`draft` and `plan`: free text in, a new task (and its first experiments) out."""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from hiraku.config import HirakuConfig
from hiraku.errors import HirakuError
from hiraku.git import GitRepository
from hiraku.record import Record, list_records
from hiraku.validation import validate_identifier

from .. import sections
from ..engine import AgentRun, Hooks
from ..errors import AgentError
from ..lock import writes
from ..replies import DraftReply
from ._common import AgentCall, _require, ensure_ids_free
from .design import run_design

# How a value the instruction left open was settled (`prompts/_assumptions.md`), as
# shown next to each default in `## Proposed protocol`.
_ASSUMPTION_LABEL = {
    "found": "found in the project",
    "default": "default, initial value",
    "choice": "recommended choice",
    "unset": "unset, needs the researcher",
}


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


@writes("draft")
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
    call = AgentCall.resolve(
        selected,
        entity=task_id,
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
        "draft",
        task_id or "new-task",
        draft_state(selected, task_id=task_id),
        instruction=instruction,
        dry_run=dry_run,
        reply=DraftReply,
    )
    if dry_run:
        return result
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


@writes("plan")
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
            f"Task {resolved!r} was written, but designing its experiments failed; "
            f"re-run `hiraku-agent design --task {resolved}`. The design step said:\n"
            f"{exc}"
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
