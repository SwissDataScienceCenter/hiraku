"""File-backed agent metadata record types (execution is out of scope)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import HirakuConfig
from .errors import ValidationError
from .records import Record, validate_identifier


class Program(Record):
    """An editable agent program record."""

    @classmethod
    def _path_for(cls, identifier: str, config: HirakuConfig, **scope: Any) -> Path:
        validate_identifier(identifier, label="program ID")
        return config.programs_dir / identifier / "program.md"

    @classmethod
    def _list_paths(cls, config: HirakuConfig, **scope: Any) -> list[Path]:
        return sorted(config.programs_dir.glob("*/program.md"))


class Context(Record):
    """A curated context record."""

    @classmethod
    def _path_for(cls, identifier: str, config: HirakuConfig, **scope: Any) -> Path:
        validate_identifier(identifier, label="context ID")
        return config.contexts_dir / identifier / "context.md"

    @classmethod
    def _list_paths(cls, config: HirakuConfig, **scope: Any) -> list[Path]:
        return sorted(config.contexts_dir.glob("*/context.md"))


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

    def _scope(self) -> dict[str, Any]:
        return {"entity": self.entity}

    def validate(self) -> None:
        super().validate()
        validate_identifier(self.entity, label="action entity")

    @classmethod
    def _path_for(cls, identifier: str, config: HirakuConfig, **scope: Any) -> Path:
        entity = scope.get("entity")
        if not entity:
            raise ValidationError(f"Entity scope required for action {identifier!r}")
        validate_identifier(entity, label="action entity")
        validate_identifier(identifier, label="action ID")
        return config.actions_dir / entity / identifier / "action.md"

    @classmethod
    def _list_paths(cls, config: HirakuConfig, **scope: Any) -> list[Path]:
        entity = scope.get("entity")
        pattern = f"{entity}/*/action.md" if entity else "*/*/action.md"
        return sorted(config.actions_dir.glob(pattern))
