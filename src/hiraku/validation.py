"""Shared Hiraku domain validation rules."""

from __future__ import annotations

import re

from .errors import ValidationError

_SAFE_ID = re.compile(r'^[^<>:"/\\|?*\x00-\x1f\x7f]+$')


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
