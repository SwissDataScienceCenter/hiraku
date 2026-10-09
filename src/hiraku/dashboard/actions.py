"""Interactive dashboard actions.

This module contains the validated record creation service used by the
interactive dashboard. It imports no marimo or pandas code so it can be tested
without the optional dashboard dependencies. Every mutating entry point
requires a :class:`DashboardCapability` and enforces it before touching files.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError as PydanticValidationError

from ..errors import HirakuError, ValidationError
from ..git import GitRepository
from ..record import Record
from ..validation import validate_identifier
from .capability import DashboardCapability
from .data import DashboardError, read_record_index

_FORBIDDEN_ADVANCED_FIELDS = frozenset(
    {"id", "parent_id", "related_to", "markdown", "path"}
)
_DEDICATED_FIELDS = frozenset(
    {
        "type",
        "commit",
        "summary",
        "goal",
        "question",
        "hypothesis",
    }
)


@dataclass(frozen=True)
class RecordDraft:
    """User-supplied record creation draft."""

    id: str
    parent_id: str | None = None
    related_to: tuple[str, ...] = ()
    type: str | None = None
    commit_ref: str | None = None
    markdown: str = ""
    summary: str | None = None
    goal: str | None = None
    question: str | None = None
    hypothesis: str | None = None
    advanced_yaml: str = ""


@dataclass(frozen=True)
class RecordDraftPreview:
    """Nonmutating validation result for a record draft."""

    id: str
    path: Path
    fields: dict[str, Any]
    errors: dict[str, str]

    @property
    def valid(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class RecordCreateResult:
    """Successful record creation outcome."""

    record: Record
    path: Path


class RecordDraftError(ValidationError):
    """A record draft is invalid; field errors are retained for the form."""

    def __init__(self, message: str, errors: dict[str, str]) -> None:
        super().__init__(message)
        self.errors = errors


def _clean(value: str | None) -> str | None:
    """Normalize optional free-text form values to None when blank."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _parse_advanced_yaml(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if not text:
        return {}
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RecordDraftError(
            "Advanced YAML is malformed",
            {"advanced_yaml": f"Malformed YAML: {exc}"},
        ) from exc
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise RecordDraftError(
            "Advanced fields must be a YAML mapping",
            {"advanced_yaml": "Advanced fields must be a YAML mapping."},
        )
    fields: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise RecordDraftError(
                "Advanced field keys must be strings",
                {"advanced_yaml": "Advanced field keys must be strings."},
            )
        fields[key] = item
    return fields


def _pydantic_errors(exc: PydanticValidationError) -> dict[str, str]:
    errors: dict[str, str] = {}
    for item in exc.errors():
        location = ".".join(str(part) for part in item["loc"])
        errors[location] = item["msg"]
    return errors


def _root_id(record_id: str, index: Mapping[str, Record]) -> str:
    seen: set[str] = set()
    current = record_id
    while True:
        if current in seen:
            raise ValidationError(f"Record parent cycle at {current!r}")
        seen.add(current)
        try:
            record = index[current]
        except KeyError as exc:
            raise ValidationError(f"Parent record {current!r} not found") from exc
        if record.parent_id is None:
            return current
        current = record.parent_id


def _preflight_tree_errors(
    index: Mapping[str, Record], records_dir: Path
) -> list[DashboardError]:
    errors: list[DashboardError] = []
    for record in index.values():
        try:
            root = _root_id(record.id, index)
        except ValidationError as exc:
            errors.append(DashboardError("record", str(exc), record_id=record.id))
            continue
        expected = records_dir / root / f"{record.id}.md"
        if record.path != expected:
            errors.append(
                DashboardError(
                    "record",
                    f"Record {record.id!r} is outside its root folder",
                    record_id=record.id,
                )
            )
        for link in record.related_to:
            if link not in index:
                errors.append(
                    DashboardError(
                        "record",
                        f"Related record {link!r} not found",
                        record_id=record.id,
                    )
                )
    return errors


def _preflight(config: Any) -> dict[str, Record]:
    index, errors = read_record_index(config)
    errors.extend(_preflight_tree_errors(index, config.records_dir))
    if errors:
        messages = [error.message for error in errors]
        raise ValidationError(
            "Project records are not safe to modify: " + " ".join(messages)
        )
    return index


def preflight_mutations(config: Any) -> dict[str, Record]:
    """Return a read-only index after blocking unsafe project states."""
    return _preflight(config)


def _field_value(draft: RecordDraft, name: str) -> str | None:
    value = getattr(draft, name)
    return _clean(value) if isinstance(value, str) else value


def validate_record_draft(config: Any, draft: RecordDraft) -> RecordDraftPreview:
    """Validate a draft without writing project files."""
    index, index_errors = read_record_index(config)
    tree_errors = _preflight_tree_errors(index, config.records_dir)
    errors: dict[str, str] = {}
    fields: dict[str, Any] = {}
    record_id = _clean(draft.id) or ""
    try:
        validate_identifier(record_id, label="record ID")
    except ValidationError as exc:
        errors["id"] = str(exc)
    if not errors.get("id") and record_id in index:
        errors["id"] = f"Record {record_id!r} already exists"

    parent_id = _clean(draft.parent_id)
    if parent_id is not None:
        try:
            validate_identifier(parent_id, label="parent ID")
        except ValidationError as exc:
            errors["parent_id"] = str(exc)
        if not errors.get("parent_id") and parent_id not in index:
            errors["parent_id"] = f"Parent record {parent_id!r} not found"
        fields["parent_id"] = parent_id

    related: list[str] = []
    for raw_link in draft.related_to:
        link = _clean(raw_link)
        if link is None:
            continue
        try:
            validate_identifier(link, label="related record ID")
        except ValidationError as exc:
            errors["related_to"] = str(exc)
            break
        if link not in index:
            errors["related_to"] = f"Related record {link!r} not found"
            break
        related.append(link)
    if len(related) != len(set(related)):
        errors["related_to"] = "related_to contains duplicate IDs"
    fields["related_to"] = related

    record_type = _field_value(draft, "type")
    if record_type is not None:
        fields["type"] = record_type

    commit_ref = _clean(draft.commit_ref)
    if commit_ref is not None:
        try:
            fields["commit"] = GitRepository(config).resolve(commit_ref)
        except HirakuError as exc:
            errors["commit_ref"] = str(exc)

    for name in ("summary", "goal", "question", "hypothesis"):
        value = _field_value(draft, name)
        if value is not None:
            fields[name] = value

    try:
        advanced = _parse_advanced_yaml(draft.advanced_yaml)
    except RecordDraftError as exc:
        advanced = {}
        errors.update(exc.errors)
    for key, value in advanced.items():
        if key in _FORBIDDEN_ADVANCED_FIELDS:
            errors["advanced_yaml"] = f"Field {key!r} cannot be set in advanced fields"
            break
        if key in _DEDICATED_FIELDS and key in fields:
            errors["advanced_yaml"] = f"Field {key!r} is already provided by the form"
            break
        fields[key] = value

    if not errors:
        try:
            Record.model_validate(
                {"id": record_id, "markdown": draft.markdown, **fields}
            )
        except PydanticValidationError as exc:
            errors.update(_pydantic_errors(exc))
        except HirakuError as exc:
            errors["advanced_yaml"] = str(exc)

    index_errors_text = " ".join(error.message for error in index_errors)
    tree_errors_text = " ".join(error.message for error in tree_errors)
    if index_errors_text or tree_errors_text:
        errors["_preflight"] = (index_errors_text + " " + tree_errors_text).strip()

    path = Path()
    if not errors and record_id:
        root = _root_id(parent_id, index) if parent_id else record_id
        path = config.records_dir / root / f"{record_id}.md"
    return RecordDraftPreview(
        id=record_id,
        path=path,
        fields=fields,
        errors=errors,
    )


def create_record(
    config: Any, draft: RecordDraft, capability: DashboardCapability
) -> RecordCreateResult:
    """Create one record, enforcing the interactive capability boundary."""
    capability.ensure_can_write()
    index = _preflight(config)
    preview = validate_record_draft(config, draft)
    if preview.errors:
        raise RecordDraftError("Record draft is invalid", preview.errors)
    # Recheck references against the current index at submission time.
    if preview.id in index:
        raise RecordDraftError(
            "Record draft is invalid",
            {"id": f"Record {preview.id!r} already exists"},
        )
    parent_id = preview.fields.get("parent_id")
    if parent_id is not None and parent_id not in index:
        raise RecordDraftError(
            "Record draft is invalid",
            {"parent_id": f"Parent record {parent_id!r} not found"},
        )
    for link in preview.fields.get("related_to", []):
        if link not in index:
            raise RecordDraftError(
                "Record draft is invalid",
                {"related_to": f"Related record {link!r} not found"},
            )
    fields = {
        key: value
        for key, value in preview.fields.items()
        if key not in {"parent_id", "related_to"}
    }
    try:
        record = Record.create(
            preview.id,
            parent_id=parent_id,
            related_to=list(preview.fields.get("related_to", [])),
            markdown=draft.markdown,
            activate=False,
            config=config,
            **fields,
        )
    except (OSError, HirakuError) as exc:
        raise RecordDraftError(
            f"Could not create record {preview.id!r}",
            {"_submit": str(exc)},
        ) from exc
    return RecordCreateResult(record=record, path=record.path or preview.path)
