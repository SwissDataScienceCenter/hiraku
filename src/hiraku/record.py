"""Validated Markdown records and their filesystem vault."""

from __future__ import annotations

import builtins
import re
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal, cast

import frontmatter
import yaml
from frontmatter.default_handlers import YAMLHandler
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)
from pydantic_core import PydanticSerializationError

from .errors import (
    RecordExistsError,
    RecordNotFoundError,
    ValidationError,
)
from .io import atomic_write
from .markdown import Markdown

_SAFE_ID = re.compile(r'^[^<>:"/\\|?*\x00-\x1f\x7f]+$')
_YAML_HANDLER = YAMLHandler()


def validate_identifier(identifier: str, *, label: str = "record ID") -> None:
    if (
        not isinstance(identifier, str)
        or not identifier
        or identifier in {".", ".."}
        or identifier != identifier.strip()
        or identifier.endswith(".")
        or not _SAFE_ID.fullmatch(identifier)
    ):
        raise ValidationError(f"Invalid {label}: {identifier!r}")


class Metric(BaseModel):
    """
    A metric declared by a research record.

    Attributes:
        name: Metric name; used to identify the metric in runs.
        direction: Metric optimization direction; either "minimize" or "maximize".
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1)
    direction: Literal["minimize", "maximize"]


def resolve_metrics(record: Record, vault: Vault) -> list[Metric]:
    """Find the nearest declared metrics, stopping at empty lists or broken edges."""
    seen = {record.id}
    while record.metrics is None and record.parent_id is not None:
        if record.parent_id in seen:
            return []
        seen.add(record.parent_id)
        try:
            record = vault.load(record.parent_id)
        except RecordNotFoundError:
            return []
    return list(record.metrics or [])


class Record(BaseModel):
    """
    A lightweight, validated research document.

    Attributes:
        id: Bundle-relative concept path, without the .md suffix.
        title: Optional OKF display title.
        markdown: Markdown content of the record.
        parent_id: Optional relative path of a parent record.
        related_to: Optional relative paths of related records.
        type: Nonblank, producer-defined OKF concept type.
        commit: Optional commit hash associated with the record.
        status: OKF concept lifecycle: draft, stable, or deprecated.
        state: Optional workflow-defined research progress.
        decision: Optional record decision.
        branch: Optional record branch.
        metrics: Optional list of metric declarations.
    """

    model_config = ConfigDict(extra="allow", validate_assignment=True)

    # OKF fields
    # Example: a concept bound to a resource (https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/main/SPEC.md)
    # ---
    # type: BigQuery Table
    # title: Customer Orders
    # description: One row per completed customer order across all channels.
    # resource: https://console.cloud.google.com/bigquery?p=acme&d=sales&t=orders
    # tags: [sales, orders, revenue]
    # generated: { by: reference_agent/gemini-2.5-pro, at: 2026-05-28T14:30:00Z }
    # ---
    type: str
    title: str | None = None
    description: str | None = None
    status: Literal["draft", "stable", "deprecated"] = "stable"
    resource: str | None = None
    tags: list[str] = Field(default_factory=list)
    generated: dict[str, Any] = Field(default_factory=dict)
    verified: list[dict[str, Any]] | None = None

    # Hiraku fields
    id: str
    markdown: Markdown = Field(default_factory=lambda: Markdown(""))
    parent_id: str | None = None
    related_to: list[str] = Field(default_factory=list)
    commit: str | None = None
    decision: str | None = None
    branch: str | None = None
    state: str | None = None
    metrics: list[Metric] | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return cast(str, cls._validate_reference_id(value, "record ID"))

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Record type must be a non-empty string")
        return value

    @field_validator("verified", mode="before")
    @classmethod
    def normalize_verified(cls, value: Any) -> Any:
        return [value] if isinstance(value, dict) else value

    @field_validator("parent_id")
    @classmethod
    def validate_parent_id(cls, value: str | None) -> str | None:
        return cls._validate_reference_id(value, "parent ID")

    @field_validator("related_to")
    @classmethod
    def validate_related_to(cls, values: list[str]) -> list[str]:
        result = [
            cast(str, cls._validate_reference_id(value, "related record ID"))
            for value in values
        ]
        if len(result) != len(set(result)):
            raise ValueError("related_to contains duplicate IDs")
        return result

    @field_validator("commit")
    @classmethod
    def validate_commit(cls, value: str | None) -> str | None:
        if value is not None and (
            len(value) not in {40, 64}
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError("commit must be a full lowercase Git hash")
        return value

    @staticmethod
    def _validate_reference_id(value: str | None, label: str) -> str | None:
        if value is None:
            return None
        try:
            for part in value.split("/"):
                validate_identifier(part, label=label)
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc
        return value

    @property
    def metadata(self) -> dict[str, Any]:
        """Return the record's serialized metadata, excluding its Markdown body."""
        try:
            metadata = self.model_dump(
                mode="json",
                exclude={"markdown", "id"},
                warnings=False,
            )
            metadata["type"] = self.type
            for field in self.model_fields_set & type(self).model_fields.keys():
                if field not in {"markdown", "id"} and getattr(self, field) is None:
                    metadata[field] = None
            return metadata
        except PydanticSerializationError as exc:
            raise ValidationError(f"Unsupported YAML value: {exc}") from exc


class Vault:
    """A Markdown bundle that owns filesystem access and workspace operations."""

    def __init__(self, *, root: Path) -> None:
        self.root = Path(root).absolute()

    def file_path(self, record_id: str) -> Path:
        """Return the contained absolute filename for a concept ID."""
        if not isinstance(record_id, str):
            raise ValidationError("Record ID must be a string")
        try:
            Record._validate_reference_id(record_id, "record ID")
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        target = self.root / f"{record_id}.md"
        if target.name.casefold() in {"index.md", "log.md"}:
            raise ValidationError(f"Reserved OKF filename: {target.name}")
        self._check_vault_path(target)
        return target

    def load(self, record_id: str) -> Record:
        """Read one document by its bundle-relative ID, without indexing the vault."""
        target = self.file_path(record_id)
        if not target.is_file():
            raise RecordNotFoundError(f"Record {record_id!r} not found")
        data, markdown = self._parse_markdown(target)
        return Record.model_validate({**data, "id": record_id, "markdown": markdown})

    def create(self, record_id: str, **fields: Any) -> Record:
        """Construct and save a document at an explicit concept ID."""
        record = Record(id=record_id, **fields)
        self.save(record)
        return record

    def list(self) -> builtins.list[Record]:
        return [
            self.load(path.relative_to(self.root).with_suffix("").as_posix())
            for path in sorted(self.root.rglob("*.md"))
            if path.name not in {"index.md", "log.md"}
        ]

    def children(self, record_id: str) -> builtins.list[Record]:
        """Return records that explicitly declare this parent ID."""
        return [record for record in self.list() if record.parent_id == record_id]

    def descendants(self, record_id: str) -> builtins.list[Record]:
        """Traverse declared parent edges, tolerating cycles and missing nodes."""
        records = self.list()
        seen = {record_id}
        pending = [record_id]
        result = []
        while pending:
            parent = pending.pop()
            for record in records:
                if record.parent_id == parent and record.id not in seen:
                    seen.add(record.id)
                    pending.append(record.id)
                    result.append(record)
        return sorted(result, key=lambda record: record.id)

    def save(self, record: Record, *, overwrite: bool = False) -> Path:
        """Validate and atomically write one record, without collection checks."""
        try:
            candidate = Record.model_validate(record.model_dump(mode="python"))
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"Invalid record {record.id!r}: {exc}") from exc
        candidate.model_fields_set.clear()
        candidate.model_fields_set.update(record.model_fields_set)
        target = self.file_path(candidate.id)
        if target.exists() and not overwrite:
            raise RecordExistsError(f"Record already exists: {target}")
        metadata = candidate.metadata
        for key in (
            "title",
            "description",
            "status",
            "resource",
            "tags",
            "generated",
            "verified",
        ):
            if key not in candidate.model_fields_set:
                metadata.pop(key, None)
        try:
            post = frontmatter.Post(str(candidate.markdown))
            post.metadata = metadata
            content = frontmatter.dumps(post, handler=_YAML_HANDLER, sort_keys=False)
            atomic_write(target, content)
        except (OSError, yaml.YAMLError) as exc:
            raise ValidationError(f"Cannot save record at {target}: {exc}") from exc
        return target

    def update(
        self,
        record: Record,
        fields: dict[str, Any],
        *,
        markdown: str | None = None,
        unset: tuple[str, ...] = (),
    ) -> Record:
        """Apply local field changes and explicitly overwrite the document."""
        if {"id", "markdown"}.intersection(fields) or "id" in unset:
            raise ValidationError("Cannot update ID or set Markdown through metadata")
        values = record.model_dump(exclude_unset=True)
        values.update(fields)
        for key in unset:
            if key in fields:
                raise ValidationError(f"Cannot set and unset {key!r} together")
            values.pop(key, None)
        if markdown is not None:
            values["markdown"] = markdown
        updated = Record.model_validate(values)
        self.save(updated, overwrite=True)
        return updated

    def delete(self, record_id: str) -> None:
        """Delete one document; dangling relationships are checked explicitly."""
        target = self.file_path(record_id)
        if not target.is_file():
            raise RecordNotFoundError(f"Record {record_id!r} not found")
        try:
            target.unlink()
            with suppress(OSError):
                target.parent.rmdir()
        except OSError as exc:
            raise ValidationError(f"Cannot delete record at {target}: {exc}") from exc

    def validate(self) -> None:
        """Explicitly check the bundle and declared relationships, allowing cycles."""
        records = self.list()
        paths = {record.id for record in records}
        for record in records:
            if record.parent_id is not None and record.parent_id not in paths:
                raise ValidationError(f"Missing parent record {record.parent_id!r}")
            if any(path not in paths for path in record.related_to):
                raise ValidationError(
                    f"Record {record.id!r} has a missing related record"
                )

    @staticmethod
    def _parse_markdown(path: Path) -> tuple[dict[str, Any], str]:
        """Parse a Markdown document's YAML front matter and body."""
        try:
            text = path.read_text(encoding="utf-8")
            if not text.startswith(("---\n", "---\r\n")) or not re.search(
                r"^---[ \t]*$", text[4:], re.M
            ):
                raise ValidationError(f"Expected YAML front matter block in {path}")
            metadata, content = frontmatter.parse(text, handler=_YAML_HANDLER)
        except (OSError, UnicodeError) as exc:
            raise ValidationError(f"Cannot read record {path}: {exc}") from exc
        except (yaml.YAMLError, TypeError, ValueError) as exc:
            raise ValidationError(
                f"Malformed YAML front matter in {path}: {exc}"
            ) from exc
        if not metadata:
            raise ValidationError(f"Expected YAML front matter mapping in {path}")
        return metadata, content

    def _check_vault_path(self, path: Path) -> None:
        try:
            path.resolve().relative_to(self.root.resolve())
        except ValueError as exc:
            raise ValidationError(f"Record path is outside vault: {path}") from exc
