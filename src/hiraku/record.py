"""Validated Markdown records and their filesystem vault."""

from __future__ import annotations

import re
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4

import frontmatter
import yaml
from frontmatter.default_handlers import YAMLHandler
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticSerializationError

from .errors import (
    AmbiguousReferenceError,
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


def _filename(record: Record) -> str:
    slug = re.sub(r"[^\w .-]+", "-", record.name).strip(" .-")
    if slug.casefold() in {"index", "log"}:
        raise ValidationError(f"Reserved OKF filename: {slug}.md")
    return f"{slug or 'record'}.md"


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


class Record(BaseModel):
    """
    A lightweight, validated research document.

    Attributes:
        id: Unique record ID.
        name: Record name; used to generate a filesystem-friendly slug.
        markdown: Markdown content of the record.
        parent_id: Optional ID of the parent record.
        related_to: Optional list of IDs of related records.
        type: Optional record type.
        commit: Optional commit hash associated with the record.
        status: Optional record status.
        decision: Optional record decision.
        branch: Optional record branch.
        metrics: Optional list of metric declarations.
        _path: Private attribute storing the record's filesystem path.
        _persisted_id: Private attribute storing the record's persisted ID.
    """

    model_config = ConfigDict(extra="allow", validate_assignment=True)

    # OKF fields
    type: str | None = None
    title: str | None = None
    description: str | None = None
    resource: str | None = None
    tags: list[str] | None = None

    # Hiraku fields
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = Field(min_length=1)
    markdown: Markdown = Markdown("")
    parent_id: str | None = None
    related_to: list[str] = Field(default_factory=list)
    commit: str | None = None
    status: str | None = None
    decision: str | None = None
    branch: str | None = None
    metrics: list[Metric] | None = None
    _path: Path | None = PrivateAttr(default=None)
    _persisted_id: str | None = PrivateAttr(default=None)
    _parent_path: Path | None = PrivateAttr(default=None)

    @model_validator(mode="before")
    @classmethod
    def default_name_from_id(cls, values: Any) -> Any:
        if isinstance(values, dict) and "name" not in values and values.get("id"):
            return {**values, "name": values["id"]}
        return values

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        try:
            validate_identifier(value)
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("record name cannot be blank")
        return value

    @field_validator("parent_id")
    @classmethod
    def validate_parent_id(cls, value: str | None) -> str | None:
        return cls._validate_reference_id(value, "parent ID")

    @field_validator("related_to")
    @classmethod
    def validate_related_to(cls, values: list[str]) -> list[str]:
        result = [
            cls._validate_reference_id(value, "related record ID") for value in values
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

    @model_validator(mode="after")
    def validate_references(self) -> Record:
        if self.parent_id == self.id or self.id in self.related_to:
            raise ValueError("A record cannot refer to itself")
        return self

    @staticmethod
    def _validate_reference_id(value: str | None, label: str) -> str | None:
        if value is None:
            return None
        try:
            validate_identifier(value, label=label)
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc
        return value

    @property
    def path(self) -> Path | None:
        return self._path

    @classmethod
    def create(
        cls,
        name: str,
        *,
        vault: Vault | None = None,
        project: Any = None,
        activate: bool = True,
        **fields: Any,
    ) -> Record:
        """Compatibility convenience that delegates creation to a vault."""
        selected_vault = vault or (project.vault if project is not None else None)
        if selected_vault is None:
            raise ValueError("Record.create requires a vault or project")
        record = selected_vault.create(name, **fields)
        if project is not None and activate:
            project.set_active_record(record)
        return record

    @property
    def metadata(self) -> dict[str, Any]:
        """Return the record's serialized metadata, excluding its Markdown body."""
        try:
            metadata = self.model_dump(
                mode="json",
                exclude={"markdown"},
                exclude_defaults=True,
                warnings=False,
            )
            for field in self.model_fields_set & type(self).model_fields.keys():
                if field != "markdown" and getattr(self, field) is None:
                    metadata[field] = None
            return metadata
        except PydanticSerializationError as exc:
            raise ValidationError(f"Unsupported YAML value: {exc}") from exc

    @classmethod
    def load(cls, path: Path) -> Record:
        """Load a validated record from a Markdown file.

        The record retains its source path and ID for subsequent vault saves.
        """
        data, markdown = cls._parse_markdown(path)
        record = cls.model_validate({**data, "markdown": markdown})
        record._path = path.absolute()
        record._persisted_id = record.id
        return record

    @staticmethod
    def _parse_markdown(path: Path) -> tuple[dict[str, Any], str]:
        """Parse a Markdown document's YAML front matter and body."""
        try:
            metadata, content = frontmatter.parse(
                path.read_text(encoding="utf-8"), handler=_YAML_HANDLER
            )
        except (OSError, UnicodeError) as exc:
            raise ValidationError(f"Cannot read record {path}: {exc}") from exc
        except yaml.YAMLError as exc:
            raise ValidationError(
                f"Malformed YAML front matter in {path}: {exc}"
            ) from exc
        if not metadata:
            raise ValidationError(f"Expected YAML front matter mapping in {path}")
        return metadata, content


class Vault:
    """Filesystem-backed collection that owns record persistence and hierarchy.

    Attributes:
        dir: Directory containing record Markdown files.

    Notes:
        A vault is ready for use as soon as it is constructed. It scans its directory
        when an operation needs the current records, so separate Vault instances do
        not retain stale collection indexes.
    """

    def __init__(self, *, dir: Path) -> None:
        self.dir = Path(dir)

    @staticmethod
    def validate_identifier(identifier: str, *, label: str = "record ID") -> None:
        validate_identifier(identifier, label=label)

    def create(self, name: str, **fields: Any) -> Record:
        record_type = fields.get("type")
        if not isinstance(record_type, str) or not record_type.strip():
            raise ValidationError("Record type must be a non-empty string")
        parent = None
        if fields.get("parent_id") is not None:
            parent = self.get(fields["parent_id"])
            fields["parent_id"] = parent.id
        if fields.get("related_to"):
            fields["related_to"] = [self.get(item).id for item in fields["related_to"]]
        record = Record(name=name, **fields)
        if parent is not None:
            record._parent_path = parent.path
            assert parent.path is not None
            label = " ".join(parent.name.split())
            label = label.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
            link = f"Parent: [{label}](../{quote(parent.path.name)})"
            body = record.markdown.rstrip()
            record.markdown = f"{body}\n\n{link}" if body else link
        self.save(record)
        return record

    def get(self, record_id: str) -> Record:
        """Return a record by ID or its exact, unambiguous name."""
        return self.find(record_id)

    def find(self, reference: str) -> Record:
        """Find by ID or exact name, rejecting ambiguous names."""
        records = self._index()
        record = records.get(reference)
        if record is not None:
            return record
        matches = [item for item in records.values() if item.name == reference]
        if len(matches) > 1:
            raise AmbiguousReferenceError(
                f"Record name {reference!r} matches multiple records"
            )
        if matches:
            return matches[0]
        raise RecordNotFoundError(f"Record {reference!r} not found")

    def _index(self) -> dict[str, Record]:
        records: dict[str, Record] = {}
        paths: dict[Path, Record] = {}
        paths_on_disk = sorted(
            self.dir.rglob("*.md"), key=lambda p: (len(p.parts), str(p))
        )
        for path in paths_on_disk:
            if path.name in {"index.md", "log.md"}:
                continue
            self._check_vault_path(path)
            if path.parent == self.dir or path.stem != path.parent.name:
                raise ValidationError(f"Expected a same-named record directory: {path}")
            record = Record.load(path)
            if record.parent_id is not None:
                raise ValidationError(f"Unexpected persisted parent_id in {path}")
            if record.id in records:
                raise ValidationError(f"Duplicate record ID: {record.id}")
            parent_dir = path.parent.parent
            if parent_dir != self.dir:
                parent_path = parent_dir / f"{parent_dir.name}.md"
                parent = paths.get(parent_path)
                if parent is None:
                    raise ValidationError(f"Missing parent record for {path}")
                record.parent_id = parent.id
                record._parent_path = parent.path
            records[record.id] = record
            paths[path] = record
        return records

    def list(self) -> list[Record]:
        return sorted(self._index().values(), key=lambda record: record.id)

    def children(self, parent_id: str) -> list[Record]:
        records = self._index()
        parent = records.get(parent_id)
        if parent is None:
            raise RecordNotFoundError(f"Record {parent_id!r} not found")
        return [record for record in records.values() if record.parent_id == parent.id]

    def descendants(self, record_id: str) -> list[Record]:
        records = self._index()
        if record_id not in records:
            raise RecordNotFoundError(f"Record {record_id!r} not found")
        return sorted(
            (
                record
                for record in records.values()
                if record.id != record_id
                and record_id in self._ancestor_ids(record, records)
            ),
            key=lambda record: record.id,
        )

    def resolve(self, record_id: str) -> dict[str, Any]:
        """Return record fields with inherited metric declarations applied."""
        records = self._index()
        record = records.get(record_id)
        if record is None:
            raise RecordNotFoundError(f"Record {record_id!r} not found")
        self._validate_records(records)
        values = record.metadata
        sources = {key: record.id for key in values}
        current = record
        seen = {record.id}
        while values.get("metrics") is None and current.parent_id is not None:
            if current.parent_id in seen:
                raise ValidationError("Record hierarchy contains a cycle")
            seen.add(current.parent_id)
            current = records.get(current.parent_id)
            if current is None:
                raise ValidationError(f"Missing parent record {record.parent_id!r}")
            if current.metrics is not None:
                values["metrics"] = [
                    item.model_dump(mode="json") for item in current.metrics
                ]
                sources["metrics"] = current.id
        return {"values": values, "sources": sources}

    def save(self, record: Record) -> Path:
        """Save a record using directory placement as its parent relationship."""
        try:
            candidate = Record.model_validate(record.model_dump(mode="python"))
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"Invalid record {record.id!r}: {exc}") from exc
        candidate.model_fields_set.clear()
        candidate.model_fields_set.update(record.model_fields_set)
        old_path = record.path
        if old_path is None and (candidate.type is None or not candidate.type.strip()):
            raise ValidationError("Record type must be a non-empty string")
        if (old_path is None) != (record._persisted_id is None):
            raise ValidationError("Record path does not match its persisted file")
        if record._persisted_id is not None and candidate.id != record._persisted_id:
            raise ValidationError("Record ID is immutable after persistence")
        parent_path = record._parent_path
        if candidate.parent_id is not None:
            if parent_path is None or not parent_path.is_file():
                raise ValidationError("Parent record path is required")
            self._check_vault_path(parent_path)
            if Record.load(parent_path).id != candidate.parent_id:
                raise ValidationError("Record parent is immutable after persistence")
        elif parent_path is not None:
            raise ValidationError("Record parent is immutable after persistence")
        filename = _filename(candidate)
        parent_dir = parent_path.parent if parent_path else self.dir
        target_dir = parent_dir / Path(filename).stem
        target = (target_dir / filename).absolute()
        self._check_vault_path(target)
        if old_path is not None:
            self._check_vault_path(old_path)
            if (
                not old_path.is_file()
                or Record.load(old_path).id != record._persisted_id
            ):
                raise ValidationError("Record path does not match its persisted file")
            if old_path != target:
                raise ValidationError("Renaming records is not supported in this PoC")
        elif target_dir.exists():
            raise RecordExistsError(f"Record already exists: {target_dir}")
        metadata = candidate.metadata
        metadata.pop("parent_id", None)
        try:
            post = frontmatter.Post(str(candidate.markdown))
            post.metadata = metadata
            content = frontmatter.dumps(post, handler=_YAML_HANDLER, sort_keys=False)
            atomic_write(target, content)
        except (OSError, yaml.YAMLError) as exc:
            raise ValidationError(f"Cannot save record at {target}: {exc}") from exc
        record._path = target
        record._persisted_id = record.id
        return target

    def delete(self, record_id: str) -> None:
        records = self._index()
        record = records.get(record_id)
        if record is None:
            raise RecordNotFoundError(f"Record {record_id!r} not found")
        assert record.path is not None
        if any(item.parent_id == record.id for item in records.values()):
            raise ValidationError(f"Cannot delete record {record.id!r} with children")
        if any(
            record.id in item.related_to
            for item in records.values()
            if item.id != record.id
        ):
            raise ValidationError(f"Cannot delete referenced record {record.id!r}")
        try:
            record.path.unlink()
            with suppress(OSError):
                record.path.parent.rmdir()
        except OSError as exc:
            raise ValidationError(
                f"Cannot delete record at {record.path}: {exc}"
            ) from exc

    def validate(self) -> None:
        records = self._index()
        self._validate_records(records)

    def validate_relationships(self, record: Record) -> None:
        """Check a proposed record's relationships against the collection."""
        records = self._index()
        self._validate_records({**records, record.id: record})

    def _validate_records(self, records: dict[str, Record]) -> None:
        for record in records.values():
            if record.parent_id is not None and record.parent_id not in records:
                raise ValidationError(f"Missing parent record {record.parent_id!r}")
            if any(link not in records for link in record.related_to):
                raise ValidationError(
                    f"Record {record.id!r} has a missing related record"
                )
            self._ancestor_ids(record, records)

    def _ancestor_ids(self, record: Record, records: dict[str, Record]) -> set[str]:
        ancestors: set[str] = set()
        current = record.parent_id
        while current is not None:
            if current in ancestors or current == record.id:
                raise ValidationError("Record hierarchy contains a cycle")
            ancestors.add(current)
            parent = records.get(current)
            if parent is None:
                break
            current = parent.parent_id
        return ancestors

    def _check_vault_path(self, path: Path) -> None:
        try:
            path.resolve().relative_to(self.dir.resolve())
        except ValueError as exc:
            raise ValidationError(f"Record path is outside vault: {path}") from exc
