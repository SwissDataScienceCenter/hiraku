from __future__ import annotations

import json
import re
import shlex
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hiraku.errors import ValidationError

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def validate_utc_timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValidationError(f"{label} must be a UTC ISO-8601 timestamp ending in 'Z'.")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValidationError(f"{label} must be a valid UTC ISO-8601 timestamp.") from exc
    if parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValidationError(f"{label} must use UTC.")
    return value


def validate_id(value: str, kind: str) -> str:
    if not ID_PATTERN.fullmatch(value):
        raise ValidationError(
            f"Invalid {kind} ID {value!r}; use lowercase letters, numbers, '.', '_' or '-'."
        )
    return value


def safe_relative(root: Path, value: str | Path, label: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        raise ValidationError(f"{label} must be relative to the project root.")
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValidationError(f"{label} must remain inside the project root.") from exc
    return resolved


def parse_invocation(value: str) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("Experiment entrypoint invocations must be non-empty strings.")
    try:
        tokens = shlex.split(value, posix=True)
    except ValueError as exc:
        raise ValidationError(f"Invalid entrypoint invocation {value!r}: {exc}") from exc
    if not tokens:
        raise ValidationError("Experiment entrypoint invocations must not be empty.")
    return tokens


def json_dump(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


def short_commit(value: Any, length: int = 10) -> Any:
    return value[:length] if isinstance(value, str) and value else value


def render_table(rows: list[dict[str, Any]], columns: list[str] | None = None) -> str:
    if not rows:
        return "No results."
    if columns is None:
        columns = []
        for row in rows:
            for key in row:
                if key not in columns:
                    columns.append(key)
    rendered = [[format_cell(row.get(column, "")) for column in columns] for row in rows]
    widths = [len(column) for column in columns]
    for row in rendered:
        widths = [max(width, len(cell)) for width, cell in zip(widths, row, strict=True)]
    header = "  ".join(column.ljust(width) for column, width in zip(columns, widths, strict=True))
    rule = "  ".join("-" * width for width in widths)
    body = [
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True))
        for row in rendered
    ]
    return "\n".join([header, rule, *body])


def format_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)
