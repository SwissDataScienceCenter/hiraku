"""File-backed agent metadata record types (execution is out of scope)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import HirakuConfig
from .errors import ValidationError
from .records import Record, validate_identifier


class Program(Record):
    """An editable agent program record."""


class Context(Record):
    """A curated context record."""


class Action(Record):
    """An entity-scoped one-step agent action record."""

    _core_fields = {"id", "entity"}

    def __init__(
        self,
        action_id: str,
        *,
        entity: str | None = None,
        markdown: str = "",
        **fields: Any,
    ) -> None:
        if entity is not None:
            fields = {"entity": entity, **fields}
        super().__init__(action_id, markdown=markdown, **fields)

    @property
    def entity(self) -> str:
        return self._data.get("entity")

    @entity.setter
    def entity(self, value: str) -> None:
        self._data["entity"] = value

    def validate(self) -> None:
        super().validate()
        validate_identifier(self.entity, label="action entity")


def load_program(program_id: str, *, config: HirakuConfig | None = None) -> Program:
    """Resolve and load a program from a Hiraku project."""
    selected = config or HirakuConfig.discover()
    validate_identifier(program_id, label="program ID")
    path = selected.programs_dir / program_id / "program.md"
    program = Program.load(path)
    _validate_registry_identity(program, program_id, path, "Program")
    return program


def list_programs(*, config: HirakuConfig | None = None) -> list[Program]:
    """List programs from a Hiraku project."""
    selected = config or HirakuConfig.discover()
    programs = Program.list(selected.programs_dir.glob("*/program.md"))
    for program in programs:
        expected = selected.programs_dir / program.id / "program.md"
        _validate_registry_identity(program, program.id, expected, "Program")
    return programs


def load_context(context_id: str, *, config: HirakuConfig | None = None) -> Context:
    """Resolve and load a context from a Hiraku project."""
    selected = config or HirakuConfig.discover()
    validate_identifier(context_id, label="context ID")
    path = selected.contexts_dir / context_id / "context.md"
    context = Context.load(path)
    _validate_registry_identity(context, context_id, path, "Context")
    return context


def list_contexts(*, config: HirakuConfig | None = None) -> list[Context]:
    """List contexts from a Hiraku project."""
    selected = config or HirakuConfig.discover()
    contexts = Context.list(selected.contexts_dir.glob("*/context.md"))
    for context in contexts:
        expected = selected.contexts_dir / context.id / "context.md"
        _validate_registry_identity(context, context.id, expected, "Context")
    return contexts


def load_action(
    action_id: str,
    *,
    entity: str,
    config: HirakuConfig | None = None,
) -> Action:
    """Resolve and load an entity-scoped action from a Hiraku project."""
    selected = config or HirakuConfig.discover()
    validate_identifier(entity, label="action entity")
    validate_identifier(action_id, label="action ID")
    path = selected.actions_dir / entity / action_id / "action.md"
    action = Action.load(path)
    _validate_registry_identity(action, action_id, path, "Action")
    if action.entity != entity:
        raise ValidationError(
            f"Action entity {action.entity!r} does not match path entity {entity!r}"
        )
    return action


def list_actions(
    *, entity: str | None = None, config: HirakuConfig | None = None
) -> list[Action]:
    """List actions, optionally restricted to one entity."""
    selected = config or HirakuConfig.discover()
    if entity is not None:
        validate_identifier(entity, label="action entity")
    pattern = f"{entity}/*/action.md" if entity else "*/*/action.md"
    actions = Action.list(selected.actions_dir.glob(pattern))
    for action in actions:
        expected = selected.actions_dir / action.entity / action.id / "action.md"
        _validate_registry_identity(action, action.id, expected, "Action")
    return actions


def _validate_registry_identity(
    record: Record, identifier: str, path: Path, label: str
) -> None:
    if record.id != identifier or record.path != path.absolute():
        raise ValidationError(f"{label} identity does not match registry path: {path}")
