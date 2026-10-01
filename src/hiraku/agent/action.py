"""Action definitions, resolution, lifecycle orchestration, and hooks."""

from __future__ import annotations

import importlib.resources
import importlib.util
import inspect
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from ..base import BaseRecord
from ..config import HirakuConfig
from ..errors import HirakuError, ValidationError
from ..record import Record, list_records, load_record
from ..state import load_type, validate_transition
from ..validation import validate_identifier
from .context import _validate_identity


class ActionError(HirakuError):
    """An action could not complete its declared step."""


@dataclass(frozen=True)
class ConditionResult:
    passed: bool
    reason: str = ""

    @classmethod
    def pass_(cls) -> ConditionResult:
        return cls(True)

    @classmethod
    def fail(cls, reason: str) -> ConditionResult:
        return cls(False, reason)


@dataclass
class ActionContext:
    config: HirakuConfig
    action: Action
    record: Record | None
    instruction: str
    runs: list[Any]
    related_records: list[Record]
    report: Callable[..., None] = field(default=lambda *args, **kwargs: None)
    log: Path | None = None
    state: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionResult:
    provider: str
    model: str | None
    origin: str
    reply: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    record: Record | None = None
    log: Path | None = None
    cleanup_error: str | None = None
    checks: dict[str, dict[str, Any]] = field(default_factory=dict)


class Action(BaseRecord):
    """A type-scoped action that creates or transitions one research record."""

    entity: str
    effect: Literal["create", "transition"] | None = None
    parent_types: list[str] = Field(default_factory=list)
    requires_prompt: bool = False
    from_status: str | None = None
    to_status: str | None = None
    hooks: str | None = None

    @model_validator(mode="after")
    def validate_action_fields(self) -> Action:
        validate_identifier(self.entity, label="action entity")
        for parent_type in self.parent_types:
            validate_identifier(parent_type, label="parent type")
        if self.effect == "create" and self.to_status is None:
            raise ValidationError("Create actions require an initial status")
        if self.effect == "transition" and self.to_status is None:
            raise ValidationError("Transition actions require a destination status")
        return self


def load_action(
    action_id: str,
    *,
    entity: str,
    config: HirakuConfig | None = None,
) -> Action:
    selected = config or HirakuConfig.discover()
    validate_identifier(entity, label="action entity")
    validate_identifier(action_id, label="action ID")
    path = selected.actions_dir / entity / action_id / "action.md"
    if not path.is_file():
        path = Path(
            str(
                importlib.resources.files("hiraku.resources")
                / "actions"
                / entity
                / action_id
                / "action.md"
            )
        )
    action = Action.load(path)
    _validate_identity(action, action_id, path, "Action")
    if action.entity != entity:
        raise ValidationError(
            f"Action entity {action.entity!r} does not match path entity {entity!r}"
        )
    return action


def action_origin(action: Action, *, config: HirakuConfig) -> str:
    path = config.actions_dir / action.entity / action.id / "action.md"
    return "project" if action.path == path.absolute() else "bundled"


def list_actions(
    *, entity: str | None = None, config: HirakuConfig | None = None
) -> list[Action]:
    selected = config or HirakuConfig.discover()
    if entity is not None:
        validate_identifier(entity, label="action entity")
    pattern = f"{entity}/*/action.md" if entity else "*/*/action.md"
    actions = Action.list(selected.actions_dir.glob(pattern))
    for action in actions:
        expected = selected.actions_dir / action.entity / action.id / "action.md"
        _validate_identity(action, action.id, expected, "Action")
    return actions


def _load_hooks(action: Action) -> Any | None:
    if action.hooks is None:
        return None
    if action.hooks != "hooks.py" or action.path is None:
        raise ValidationError("Action hooks must declare the local file hooks.py")
    path = action.path.parent / action.hooks
    if not path.is_file():
        raise ValidationError(f"Declared action hooks not found: {path}")
    name = f"_hiraku_action_{action.entity}_{action.id}_{id(action)}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValidationError(f"Cannot load action hooks: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for phase, count in {
        "precondition": 1,
        "before": 1,
        "after": 2,
        "postcondition": 2,
        "cleanup": 3,
    }.items():
        hook = getattr(module, phase, None)
        if hook is None:
            continue
        if not inspect.isfunction(hook) or inspect.iscoroutinefunction(hook):
            raise ValidationError(f"Hook {phase} must be a synchronous function")
        try:
            inspect.signature(hook).bind(*([None] * count))
        except TypeError as exc:
            raise ValidationError(f"Hook {phase} has an invalid signature") from exc
    return module


def _condition(
    hooks: Any, phase: str, checks: dict[str, dict[str, Any]], *args: Any
) -> None:
    hook = getattr(hooks, phase, None) if hooks else None
    if hook is None:
        return
    result = hook(*args)
    if not isinstance(result, ConditionResult):
        raise ActionError(f"Hook {phase} must return ConditionResult")
    checks[phase] = {"passed": result.passed, "reason": result.reason}
    args[0].report(
        "check",
        f"{phase}: {'passed' if result.passed else 'failed'}",
        reason=result.reason,
    )
    if not result.passed:
        raise ActionError(result.reason or f"{phase} failed")


def _prompt(context: ActionContext) -> str:
    records = list_records(config=context.config)
    if context.action.entity == "task":
        state: dict[str, Any] = {
            "existing_tasks": [record.id for record in records if record.type == "task"]
        }
    else:
        task = context.record
        while task and task.type != "task" and task.parent_id:
            task = load_record(task.parent_id, config=context.config)
        tree = [
            record
            for record in records
            if task
            and (record.id == task.id or _is_descendant(record, task.id, records))
        ]
        state = {
            "task": _record_data(task),
            "selected_parent": _record_data(context.record),
            "records": [_record_data(record) for record in tree],
            "runs": [
                {
                    "id": run.id,
                    "record_id": run.record_id,
                    "metrics": dict(run.metrics),
                    "params": dict(run.params),
                }
                for record in tree
                for run in record.runs(config=context.config)
                if run.record_id == record.id
            ],
        }
    prompt = context.action.markdown
    prompt = prompt.replace("{{state}}", json.dumps(state, default=str, indent=2))
    prompt = prompt.replace("{{instruction}}", context.instruction)
    if re.search(r"\{\{[^{}]+}}", prompt):
        raise ValidationError("Action prompt contains an unknown template variable")
    return prompt


def _record_data(record: Record | None) -> dict[str, Any] | None:
    return {**record.to_dict(), "markdown": record.markdown} if record else None


def _is_descendant(record: Record, root_id: str, records: list[Record]) -> bool:
    by_id = {item.id: item for item in records}
    parent_id = record.parent_id
    while parent_id:
        if parent_id == root_id:
            return True
        parent = by_id.get(parent_id)
        parent_id = parent.parent_id if parent else None
    return False


def _parse_reply(reply: str) -> dict[str, Any]:
    text = reply.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ActionError("The agent did not return a JSON object") from exc
    if not isinstance(data, dict):
        raise ActionError("The agent reply must be a JSON object")
    return data


def _apply_result(context: ActionContext, result: ActionResult) -> Record:
    action = context.action
    if action.to_status is None:
        raise ValidationError("Executable action requires to_status")
    if action.effect == "create":
        identifier = result.data.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ActionError("Create action reply requires a record id")
        allowed_statuses = load_type(action.entity, config=context.config)
        if action.to_status not in {
            allowed_statuses.initial_status,
            *allowed_statuses.lifecycle,
        }:
            raise ValidationError("Create action has an invalid initial status")
        reserved = {"id", "type", "status", "parent_id", "related_to", "path", "commit"}
        if reserved.intersection(result.data) != {"id"}:
            raise ActionError("Create action reply contains reserved record fields")
        if context.record and action.parent_types:
            if context.record.type not in action.parent_types:
                raise ValidationError(
                    f"Action {action.entity}/{action.id} cannot create under "
                    f"{context.record.type!r}"
                )
            parent_id = context.record.id
        elif context.record is None and not action.parent_types:
            parent_id = None
        else:
            raise ValidationError(
                "This create action requires a selected parent record"
            )
        markdown = result.data.get("markdown") or ""
        fields = {
            key: value
            for key, value in result.data.items()
            if key not in {"id", "markdown"}
        }
        created = Record.create(
            identifier,
            parent_id=parent_id,
            type=action.entity,
            status=action.to_status,
            activate=action.entity == "task",
            config=context.config,
            markdown=markdown,
            **fields,
        )
        context.report(
            "record_created",
            f"Created {created.type} {created.id}",
            parent_id=parent_id,
            status=created.status,
        )
        return created
    if context.record is None:
        raise ValidationError("Transition action requires a selected record")
    live = load_record(context.record.id, config=context.config)
    if live.type != action.entity:
        raise ValidationError(
            f"Action {action.entity}/{action.id} needs a {action.entity} record"
        )
    if live.status != context.record.status:
        raise ActionError("Agent changed status; Hiraku owns the transition")
    validate_transition(live.type, live.status, action.to_status, config=context.config)
    live.status = action.to_status
    live.save()
    context.report(
        "status_changed", f"{live.id}: {context.record.status} → {live.status}"
    )
    return live


def run_action(
    entity: str,
    action_id: str,
    *,
    record_id: str | None,
    instruction: str = "",
    provider: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    config: HirakuConfig | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> ActionResult:
    """Execute an action and apply its declared record change."""
    from .provider import call_provider, provider_command

    selected = config or HirakuConfig.discover()
    action = load_action(action_id, entity=entity, config=selected)
    if action.requires_prompt and not instruction.strip():
        raise ValidationError(f"Action {entity}/{action_id} requires -m/--message")
    target = load_record(record_id, config=selected) if record_id else None
    if action.effect == "create":
        if action.parent_types and target is None:
            raise ValidationError(
                "This create action requires --record or an active record"
            )
        if not action.parent_types and target is not None:
            raise ValidationError(
                "This create action creates a root record without a parent"
            )
        if target is not None and target.type not in action.parent_types:
            raise ValidationError(
                f"Action {entity}/{action_id} cannot create under {target.type!r}"
            )
        definition = load_type(entity, config=selected)
        if action.to_status not in {definition.initial_status, *definition.lifecycle}:
            raise ValidationError("Create action has an invalid initial status")
    elif action.effect == "transition" and (target is None or target.type != entity):
        raise ValidationError(f"Action {entity}/{action_id} requires a {entity} record")
    elif action.effect is None:
        raise ValidationError(f"Action {entity}/{action_id} is not executable")
    if (
        action.from_status is not None
        and target
        and target.status != action.from_status
    ):
        raise ValidationError(
            f"Action {entity}/{action_id} requires status {action.from_status!r}"
        )
    if action.effect == "transition" and target:
        validate_transition(
            target.type, target.status, action.to_status, config=selected
        )
    hooks = _load_hooks(action)
    records = list_records(config=selected)
    context = ActionContext(
        config=selected,
        action=action,
        record=target,
        instruction=instruction,
        runs=(
            [run for run in target.runs(config=selected) if run.record_id == target.id]
            if target
            else []
        ),
        related_records=(
            [
                item
                for item in records
                if target
                and (
                    item.id in target.related_to
                    or item.id == target.parent_id
                    or item.parent_id == target.id
                )
            ]
        ),
    )
    resolved_provider = provider or selected.agent_provider
    resolved_model = model or (
        selected.agent_model if resolved_provider == selected.agent_provider else None
    )
    resolved_effort = reasoning_effort or (
        selected.agent_reasoning_effort
        if resolved_provider == selected.agent_provider
        else None
    )
    provider_command(resolved_provider, resolved_model, resolved_effort)
    result = ActionResult(
        provider=resolved_provider,
        model=resolved_model,
        origin=action_origin(action, config=selected),
    )
    log_dir = selected.hiraku_dir / "agent-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    target_id = target.id if target else "root"
    result.log = log_dir / f"{stamp}-{entity}-{action_id}-{target_id}"
    result.log.mkdir()
    context.log = result.log

    def report(kind: str, message: str, **details: Any) -> None:
        event = {
            "time": datetime.now(UTC).isoformat(),
            "type": kind,
            "message": message,
            **details,
        }
        with (result.log / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, default=str) + "\n")
        if on_event:
            on_event(event)

    context.report = report
    report(
        "action_started",
        f"{entity}/{action_id}: {result.origin} action; "
        f"{resolved_provider}, model {resolved_model or 'provider default'}",
    )
    prompt = _prompt(context)
    (result.log / "prompt.md").write_text(prompt, encoding="utf-8")
    error: BaseException | None = None
    try:
        _condition(hooks, "precondition", result.checks, context)
        if hooks and hasattr(hooks, "before"):
            hooks.before(context)
        result.reply = call_provider(
            resolved_provider,
            resolved_model,
            prompt,
            selected.root,
            resolved_effort,
            context=context,
            report=report,
        )
        report("provider_completed", "Agent work finished")
        (result.log / "reply.txt").write_text(result.reply, encoding="utf-8")
        if action.effect == "create":
            result.data = _parse_reply(result.reply)
        if hooks and hasattr(hooks, "after"):
            hooks.after(context, result)
        _condition(hooks, "postcondition", result.checks, context, result)
        result.record = _apply_result(context, result)
    except BaseException as exc:
        error = exc
        report("action_failed", str(exc))
        (result.log / "error.txt").write_text(str(exc), encoding="utf-8")
        if action.effect == "transition" and target:
            live = load_record(target.id, config=selected)
            if live.status != target.status:
                live.status = target.status
                live.save()
        raise
    finally:
        if hooks and hasattr(hooks, "cleanup"):
            try:
                hooks.cleanup(context, result if result.reply else None, error)
            except Exception as exc:
                result.cleanup_error = str(exc)
        (result.log / "meta.json").write_text(
            json.dumps(
                {
                    "action": f"{entity}/{action_id}",
                    "target": target.id if target else None,
                    "origin": result.origin,
                    "provider": resolved_provider,
                    "model": resolved_model,
                    "error": str(error) if error else None,
                    "reasoning_effort": resolved_effort,
                    "checks": result.checks,
                    "cleanup_error": result.cleanup_error,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    return result
