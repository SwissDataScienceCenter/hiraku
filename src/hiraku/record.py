"""Validated research records and persistence for native fmql workspaces."""

from __future__ import annotations

import re
import warnings
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any, Literal, Self

from fmql import (
    CypherResult,
    IdResolver,
    Packet,
    Query,
    Workspace,
    parse,
    serialize,
)
from fmql.cypher.compile import parse_cypher
from fmql.cypher.executor import compile_cypher_ast
from fmql.errors import FmqlError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from .config import HirakuConfig
from .errors import RecordExistsError, RecordNotFoundError, ValidationError
from .io import atomic_write
from .markdown import Markdown
from .run import Run
from .tracking import TrackingBackend, create_backend

_SAFE_ID = re.compile(r'^[^<>:"/\\|?*\x00-\x1f\x7f]+$')


class Metric(BaseModel):
    """A declared metric and its preferred direction."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    direction: Literal["minimize", "maximize"]


class Record(BaseModel):
    """Validated research metadata and Markdown from an fmql packet.

    Identity comes from frontmatter, independently of the user-owned file path.
    Relationships do not imply inheritance or filesystem placement.
    """

    model_config = ConfigDict(
        extra="allow",
        validate_assignment=True,
        validate_by_name=True,
    )

    id: str
    markdown: Markdown = Markdown("")

    # Persistence metadata (not part of the record's data)
    path: Path | None = Field(default=None, exclude=True)

    parent_id: str | None = None
    related_to: list[str] = Field(default_factory=list)
    type: str | None = None
    commit: str | None = None
    status: str | None = None
    decision: str | None = None
    branch: str | None = None
    metrics: list[Metric] | None = None

    @field_validator("id", "parent_id")
    @classmethod
    def validate_id(cls, value: str | None) -> str | None:
        if value is not None and (
            not value
            or value in {".", ".."}
            or value != value.strip()
            or value.endswith(".")
            or not _SAFE_ID.fullmatch(value)
        ):
            raise ValueError(f"Invalid record ID: {value!r}")
        return value

    @field_validator("related_to")
    @classmethod
    def validate_links(cls, value: list[str]) -> list[str]:
        for identifier in value:
            cls.validate_id(identifier)
        if len(value) != len(set(value)):
            raise ValueError("related_to contains duplicate IDs")
        return value

    @field_validator("commit")
    @classmethod
    def validate_commit(cls, value: str | None) -> str | None:
        if value is not None and (
            len(value) not in {40, 64}
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValidationError("commit must be a full lowercase Git hash")
        return value

    @model_validator(mode="after")
    def validate_research_fields(self) -> Self:
        if self.parent_id == self.id or self.id in self.related_to:
            raise ValidationError("A record cannot refer to itself")
        return self

    @model_validator(mode="after")
    def validate_relationships(self, info: ValidationInfo) -> Self:
        workspace = (info.context or {}).get("workspace")
        if workspace is None:
            return self
        for target in [self.parent_id, *self.related_to]:
            if target is not None:
                matches = list(Query(workspace).where(id=target))
                if len(matches) != 1:
                    raise ValueError(
                        f"Referenced record {target!r} must exist uniquely"
                    )
        ancestors = (
            Query(workspace)
            .where(id=self.parent_id)
            .follow("parent_id", depth="*", include_origin=True)
            if self.parent_id is not None
            else ()
        )
        if any(packet.frontmatter.get("id") == self.id for packet in ancestors):
            raise ValueError("Record parent cycle")
        return self

    @model_validator(mode="before")
    @classmethod
    def validate_storage_fields(cls, values: Any, info: ValidationInfo) -> Any:
        if (info.context or {}).get("creating") and "path" in values:
            raise ValueError("Record path is determined by the creation destination")
        if isinstance(values, Mapping) and "tracking_backend" in values:
            raise ValidationError(
                "tracking_backend must be configured in hiraku.yml, "
                "not record front matter"
            )
        return values

    @model_validator(mode="after")
    def validate_yaml_fields(self) -> Self:
        type(self)._validate_yaml_value(self.to_dict())
        return self

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "tracking_backend":
            raise ValidationError(
                "tracking_backend must be configured in hiraku.yml, not on a record"
            )
        super().__setattr__(name, value)

    def to_dict(self) -> dict[str, Any]:
        """Return the serialized record fields, excluding persistence metadata."""
        return self.model_dump(
            exclude={"markdown", "path"},
            exclude_none=False,
            exclude_unset=True,
        )

    @classmethod
    def _validate_yaml_value(cls, value: Any, path: str = "field") -> None:
        """Validate that a value can be represented in YAML front matter."""
        if value is None or isinstance(value, (str, int, float, bool, date)):
            return
        if isinstance(value, list | tuple):
            for index, item in enumerate(value):
                cls._validate_yaml_value(item, f"{path}[{index}]")
            return
        if isinstance(value, Mapping):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ValidationError(f"Unsupported non-string YAML key in {path}")
                cls._validate_yaml_value(item, f"{path}.{key}")
            return
        raise ValidationError(
            f"Unsupported YAML value in {path}: {type(value).__name__}"
        )

    def update(
        self,
        workspace: Workspace,
        fields: Mapping[str, Any] | None = None,
        *,
        unset: Sequence[str] = (),
        markdown: str | None = None,
        **changes: Any,
    ) -> Record:
        """Merge metadata, remove fields, and save a validated copy of a record."""
        changes = {**(fields or {}), **changes}
        invalid = {"id", "path", "markdown"}.intersection(changes)
        invalid.update({"id", "path", "markdown"}.intersection(unset))
        if invalid:
            raise ValidationError(f"Cannot update fields: {sorted(invalid)}")
        if set(changes).intersection(unset):
            raise ValidationError("Cannot set and unset the same field")
        values = {**self.to_dict(), **changes}
        for key in unset:
            values.pop(key, None)
        values["markdown"] = self.markdown if markdown is None else markdown
        updated = type(self).model_validate(values)
        updated.path = self.path
        updated.save(workspace)
        return updated

    @classmethod
    def create(
        cls,
        workspace: Workspace,
        destination: Path | str,
        *,
        id: str | None = None,
        **fields: Any,
    ) -> Self:
        """Create a record at an explicit destination, inferring ID from its stem."""
        destination = Path(destination).expanduser()
        if not destination.name or destination.name == "..":
            raise ValidationError("Destination must name a Markdown file")
        if not destination.suffix:
            destination = destination.with_suffix(".md")
        record = cls.model_validate(
            {**fields, "id": destination.stem if id is None else id},
            context={"creating": True},
        )
        record.save(workspace, destination)
        return record

    def save(self, workspace: Workspace, path: Path | str | None = None) -> Path:
        """Validate and save; a new record requires an explicit destination.

        The workspace is refreshed before validation and after a successful write.
        Each file write is atomic; the workspace is not a transaction snapshot.
        """
        destination = path if path is not None else self.path
        if destination is None:
            raise ValidationError("An explicit destination path is required")
        target = (workspace.root / Path(destination).expanduser()).resolve()
        if not target.is_relative_to(workspace.root) or target.suffix != ".md":
            raise ValidationError(
                "Destination must be a Markdown file in the workspace"
            )
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            try:
                workspace.rescan()
            except (Warning, OSError, ValueError, FmqlError) as exc:
                raise ValidationError(f"Cannot read knowledge base: {exc}") from exc
        existing = list(Query(workspace).where(id=self.id))
        if len(existing) > 1:
            raise ValidationError(f"Duplicate record ID {self.id!r}")
        if existing and existing[0].abspath != target:
            raise RecordExistsError(f"Record {self.id!r} already exists")
        if target.exists() and self.path != target:
            raise RecordExistsError(f"Destination already exists: {target}")
        pid = target.relative_to(workspace.root).as_posix()
        packet = deepcopy(workspace.get(pid)) or parse("", abspath=target, pid=pid)
        if packet.frontmatter.get("id", self.id) != self.id:
            raise ValidationError("Cannot replace another record's identity")
        values = (
            type(self)
            .model_validate(
                self.model_dump(exclude_unset=True), context={"workspace": workspace}
            )
            .to_dict()
        )
        for key in list(packet.frontmatter):
            if key not in values:
                del packet.frontmatter[key]
        packet.frontmatter.update(values)
        packet.body = str(self.markdown)
        packet.newline_at_eof = not packet.body or packet.body.endswith(packet.eol)
        packet.has_frontmatter = True
        _write_packet(workspace, packet)
        self.path = target
        return target

    def delete(self, workspace: Workspace) -> None:
        """Delete an unreferenced record without touching its directory."""
        workspace.rescan()
        current = Record.load(workspace, self.id)
        if current.path != self.path:
            raise ValidationError("Cannot delete a stale record; reload it")
        if list(Query(workspace).where(parent_id=self.id)) or list(
            Query(workspace).where(related_to__contains=self.id)
        ):
            raise ValidationError(f"Cannot delete referenced record {self.id!r}")
        assert current.path is not None
        try:
            current.path.unlink()
        except OSError as exc:
            raise ValidationError(
                f"Cannot delete record at {current.path}: {exc}"
            ) from exc
        self.path = None
        workspace.rescan()

    @classmethod
    def load(cls, workspace: Workspace, identifier: str) -> Self:
        """Load a record by unique frontmatter ID using fmql."""
        packets = list(Query(workspace).where(id=identifier))
        if not packets:
            raise RecordNotFoundError(f"Record {identifier!r} not found")
        if len(packets) != 1:
            raise ValidationError(f"Duplicate record ID {identifier!r}")
        return cls.from_packet(packets[0])

    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        """Validate a packet's metadata without interpreting its location."""
        return cls.model_validate(
            {**packet.frontmatter, "markdown": packet.body, "path": packet.abspath}
        )

    def save_markdown(self, workspace: Workspace, markdown: Markdown) -> Record:
        """Save an edited copy while rejecting changes made since this was read."""
        if self.path is None:
            raise ValidationError("Save this record before editing it")
        workspace.rescan()
        try:
            current = Record.load(workspace, self.id)
        except RecordNotFoundError as exc:
            raise RecordNotFoundError(
                f"Record {self.id!r} not found at {self.path}"
            ) from exc
        if (
            current.path != self.path
            or current.to_dict() != self.to_dict()
            or current.markdown != self.markdown
        ):
            raise ValidationError("This record changed on disk. Reload before saving.")
        current.markdown = markdown
        packet = workspace.get(current.path.relative_to(workspace.root).as_posix())
        assert packet is not None
        packet = deepcopy(packet)
        packet.body = str(current.markdown)
        packet.newline_at_eof = not packet.body or packet.body.endswith(packet.eol)
        _write_packet(workspace, packet)
        return current

    def tracking_backend_spec(self, *, config: HirakuConfig) -> dict[str, Any]:
        """Return the project-wide tracking backend configuration."""
        specification = config.tracking_backend
        if isinstance(specification, str):
            return {"type": specification}
        return dict(specification)

    def resolve_tracking_backend(self, *, config: HirakuConfig) -> TrackingBackend:
        """Create the tracking backend configured for this project."""
        return create_backend(self.tracking_backend_spec(config=config), config)

    def runs(
        self,
        *,
        config: HirakuConfig,
        sort: str | None = None,
        ascending: bool | None = None,
        limit: int | None = None,
    ) -> list[Run]:
        """Return runs attributed to this record.

        Sorting and limiting use the same options as `TrackingBackend.list_runs`.
        """
        return self.resolve_tracking_backend(config=config).list_runs(
            record_id=self.id,
            sort=sort,
            ascending=ascending,
            limit=limit,
        )


def open_workspace(config: HirakuConfig) -> Workspace:
    """Open the configured knowledge base as an actual fmql Workspace."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        try:
            return Workspace(
                config.records_dir,
                resolvers={"parent_id": IdResolver(), "related_to": IdResolver()},
            )
        except (Warning, OSError, ValueError, FmqlError) as exc:
            raise ValidationError(f"Cannot read knowledge base: {exc}") from exc


def list_records(workspace: Workspace) -> list[Record]:
    """Read validated metadata views of packets with a frontmatter ID."""
    return [
        Record.from_packet(p)
        for p in Query(workspace).where(id__exists=True).order_by("id")
    ]


def resolve_metrics(record: Record, workspace: Workspace) -> list[Metric]:
    """Return the nearest defined metric list, following parents through fmql.

    An empty list stops the lookup; None continues to the parent.
    The supplied workspace is used without refreshing it.
    """
    seen: set[str] = set()
    while record.id not in seen:
        if record.metrics is not None:
            return record.metrics
        seen.add(record.id)
        parents = list(Query(workspace).where(id=record.id).follow("parent_id"))
        if not parents:
            return []
        if len(parents) != 1:
            raise ValidationError(f"Ambiguous parent for record {record.id!r}")
        record = Record.from_packet(parents[0])
    raise ValidationError("Record parent cycle")


def query_records(workspace: Workspace, text: str) -> CypherResult:
    """Enforce read-only fmql queries at the CLI/MCP boundary."""
    try:
        ast = parse_cypher(text)
        if ast.set_items or ast.remove_items:
            raise ValidationError("Queries are read-only; use validated record writes")
        result = compile_cypher_ast(ast, workspace).result
    except FmqlError as exc:
        raise ValidationError(f"Invalid fmql query: {exc}") from exc
    if result is None:
        raise ValidationError("A query requires RETURN")
    return result


def validate_records(workspace: Workspace) -> None:
    """Check metadata, unique IDs, reference targets, and parent cycles."""
    records = list_records(workspace)
    ids: set[str] = set()
    for record in records:
        if record.id in ids:
            raise ValidationError(f"Duplicate record ID {record.id!r}")
        ids.add(record.id)
    for record in records:
        for target in [record.parent_id, *record.related_to]:
            if target is not None and target not in ids:
                raise RecordNotFoundError(f"Referenced record {target!r} not found")
    if list(Query(workspace).cypher("MATCH (r)-[:parent_id*]->(r) RETURN r")):
        raise ValidationError("Record parent cycle")


def _write_packet(workspace: Workspace, packet: Packet) -> None:
    try:
        atomic_write(packet.abspath, serialize(packet))
    except OSError as exc:
        raise ValidationError(f"Cannot save record at {packet.abspath}: {exc}") from exc
    workspace.rescan()
