"""Project-overridable lifecycle definitions for typed records."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from pydantic import model_validator

from .base import BaseRecord
from .config import HirakuConfig
from .errors import ValidationError
from .io import load_yaml_mapping
from .validation import validate_identifier


class RecordType(BaseRecord):
    """A documented record type with an initial status and lifecycle labels."""

    initial_status: str
    lifecycle: list[str]

    @model_validator(mode="after")
    def validate_lifecycle(self) -> RecordType:
        validate_identifier(self.id, label="record type")
        validate_identifier(self.initial_status, label="initial status")
        if not self.lifecycle or len(self.lifecycle) != len(set(self.lifecycle)):
            raise ValidationError("Type lifecycle must be non-empty and unique")
        for status in self.lifecycle:
            validate_identifier(status, label="status")
        if {"statuses", "transitions"}.intersection(self.model_extra or {}):
            raise ValidationError(
                "Type definitions use lifecycle, not statuses/transitions"
            )
        return self


def load_type(record_type: str, *, config: HirakuConfig | None = None) -> RecordType:
    """Resolve a project type first, falling back to the bundled definition."""
    selected = config or HirakuConfig.discover()
    validate_identifier(record_type, label="record type")
    bundled = Path(str(files("hiraku.resources") / "types"))
    path = next(
        (
            directory / f"{record_type}{suffix}"
            for directory in (selected.types_dir, bundled)
            for suffix in (".md", ".yml")
            if (directory / f"{record_type}{suffix}").is_file()
        ),
        None,
    )
    if path is None:
        raise ValidationError(
            f"No lifecycle is defined for record type {record_type!r}"
        )
    definition = (
        RecordType.load(path)
        if path.suffix == ".md" or path.read_text(encoding="utf-8").startswith("---")
        else RecordType._construct_loaded(load_yaml_mapping(path), "", path.absolute())
    )
    if definition.id != record_type:
        raise ValidationError(f"Type ID does not match filename: {path}")
    return definition


def list_types(*, config: HirakuConfig | None = None) -> list[RecordType]:
    """List resolved project and bundled types, with project overrides first."""
    selected = config or HirakuConfig.discover()
    bundled = Path(str(files("hiraku.resources") / "types"))
    identifiers = {
        path.stem
        for directory in (selected.types_dir, bundled)
        if directory.is_dir()
        for path in directory.iterdir()
        if path.is_file() and path.suffix in {".md", ".yml"}
    }
    return [
        load_type(identifier, config=selected) for identifier in sorted(identifiers)
    ]


def validate_transition(
    record_type: str,
    source: str | None,
    destination: str,
    *,
    config: HirakuConfig | None = None,
) -> None:
    """Validate action endpoints against a type's labels, without an edge graph."""
    definition = load_type(record_type, config=config)
    labels = {definition.initial_status, *definition.lifecycle}
    if source not in labels or destination not in labels or source == destination:
        raise ValidationError(
            f"Invalid {record_type} transition: {source!r} -> {destination!r}"
        )
