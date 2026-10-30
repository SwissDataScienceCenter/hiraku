"""Shared Hiraku domain validation rules."""

from __future__ import annotations

import re
from typing import Any

from .errors import ValidationError

_SAFE_ID = re.compile(r'^[^<>:"/\\|?*\x00-\x1f\x7f]+$')


def validate_command(value: Any) -> list[str] | None:
    """Validate command configuration as a literal argv sequence."""
    if value is None:
        return None
    if isinstance(value, str | bytes) or not isinstance(value, list | tuple):
        raise ValidationError("command must be an argv sequence")
    command = list(value)
    if not command or not isinstance(command[0], str) or not command[0]:
        raise ValidationError("command executable must be a non-empty string")
    if any(not isinstance(argument, str) or "\x00" in argument for argument in command):
        raise ValidationError("command arguments must be strings without NUL bytes")
    return command


def validate_identifier(identifier: str, *, label: str = "record ID") -> None:
    """
    Validate an identifier used in Hiraku registries and references.

    Args:
        identifier: The identifier string to validate.
        label: A descriptive label for the identifier, used in error messages.

    Raises:
        ValidationError: If the identifier is invalid according to Hiraku's rules.
    """
    if (
        not isinstance(identifier, str)
        or not identifier
        or identifier in {".", ".."}
        or identifier != identifier.strip()
        or identifier.endswith(".")
        or not _SAFE_ID.fullmatch(identifier)
    ):
        raise ValidationError(f"Invalid {label}: {identifier!r}")
