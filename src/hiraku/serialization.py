"""Conversion helpers for rendering Hiraku values as JSON-compatible data."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .base import BaseRecord
from .run import Run


def _json_value(value: Any) -> Any:
    """Convert records, runs, and nested values to JSON-compatible data."""
    if isinstance(value, BaseRecord):
        return {**_json_value(value.to_dict()), "markdown": value.markdown}
    if isinstance(value, Run):
        return {
            "id": value.id,
            "metrics": _json_value(value.metrics),
            "params": _json_value(value.params),
            "record_id": value.record_id,
            "commit": value.commit,
            "timestamp": _json_value(value.timestamp),
            "entrypoint": value.entrypoint,
            "url": value.url,
            "raw": _json_value(value.raw),
        }
    if isinstance(value, BaseModel):
        return _json_value(value.model_dump())
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [_json_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value
