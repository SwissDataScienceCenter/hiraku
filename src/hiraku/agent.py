"""File-backed curated context records for coding agents."""

from __future__ import annotations

from pathlib import Path

from .base import BaseRecord
from .config import HirakuConfig
from .errors import ValidationError
from .validation import validate_identifier


class Context(BaseRecord):
    """A curated context record."""


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


def _validate_registry_identity(
    record: BaseRecord, identifier: str, path: Path, label: str
) -> None:
    if record.id != identifier or record.path != path.absolute():
        raise ValidationError(f"{label} identity does not match registry path: {path}")
