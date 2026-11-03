"""Attribution supplied to tracking integrations by a Hiraku launch."""

import os
from typing import Any

from ..errors import ValidationError


def _tracking_metadata(values: dict[str, Any] | None) -> dict[str, Any]:
    """Add available Hiraku job metadata to the native tracker's mapping."""
    result = dict(values or {})
    for field in ("job_id", "record_id", "commit", "git_state"):
        value = os.environ.get(f"HIRAKU_{field.upper()}")
        if value:
            key = f"hiraku.{field}"
            if key in result and result[key] != value:
                raise ValidationError(f"Conflicting attribution for {key}")
            result[key] = value
    return result
