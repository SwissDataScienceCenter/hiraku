"""Research records, commands, and metric declarations."""

from __future__ import annotations

import builtins
import json
import re
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)

from .errors import RecordExistsError, RecordNotFoundError, ValidationError
from .io import atomic_write, dump_yaml
from .markdown import Markdown
from .run import Run
from .tracking import TrackingBackend, create_backend

_FRONT_MATTER = re.compile(
    r"\A---\r?\n(.*?)^---[ \t]*(?:\r?\n|\Z)", re.MULTILINE | re.DOTALL
)
_SAFE_ID = re.compile(r'^[^<>:"/\\|?*\x00-\x1f\x7f]+$')


def validate_identifier(identifier: str, *, label: str = "record ID") -> None:
    """Validate an identifier used in record paths and references."""
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
    """A declared metric and its preferred direction."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    direction: Literal["minimize", "maximize"]


_LOCAL_FIELDS = frozenset(
    {"id", "parent_id", "related_to", "type", "commit", "status", "decision"}
)
_MATERIALIZED_INHERITED_FIELDS = ("metrics",)


@dataclass(frozen=True)
class ResolvedRecord:
    values: dict[str, Any]
    sources: dict[str, Any]


class Record(BaseModel):
    """A research record stored as Markdown with YAML front matter.

    Attributes:
        id: Stable identifier used in the record path.
        markdown: Free-form Markdown body; a `Markdown` string, whose sections
            can be selected by heading, e.g. ``record.markdown.section("## Change")``.
        path: Persistence path, populated after saving or loading.

    Example:
        ``Record(id="demo", markdown="## Objective\\nMeasure accuracy.")``

    Raises:
        pydantic.ValidationError: If the identifier or Markdown value is invalid.
        hiraku.errors.ValidationError: If a persisted record is mutated illegally.

    Note:
        Records are detached until ``save()`` is called; custom fields are
        preserved in ``model_extra``.

    Warning:
        A persisted record cannot be moved to a different path or renamed by
        changing its identifier.

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
    _persisted_id: str | None = PrivateAttr(default=None)

    parent_id: str | None = None
    related_to: builtins.list[str] = Field(default_factory=list)
    type: str | None = None
    commit: str | None = None
    status: str | None = None
    decision: str | None = None
    branch: str | None = None
    metrics: builtins.list[Metric] | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise TypeError("record ID must be a string")
        validate_identifier(value, label="record ID")
        return value

    @field_validator("parent_id")
    @classmethod
    def validate_parent(cls, value: str | None) -> str | None:
        if value is not None:
            validate_identifier(value, label="parent ID")
        return value

    @field_validator("related_to")
    @classmethod
    def validate_links(cls, value: list[str]) -> list[str]:
        for identifier in value:
            validate_identifier(identifier, label="related record ID")
        if len(value) != len(set(value)):
            raise ValidationError("related_to contains duplicate IDs")
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

    @model_validator(mode="before")
    @classmethod
    def reject_record_tracking_backend(cls, values: Any) -> Any:
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
        if name == "id":
            persisted_id = getattr(self, "_persisted_id", None)
            if persisted_id is not None and value != persisted_id:
                raise ValidationError(
                    f"Record ID is immutable after persistence: {persisted_id}"
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
        if value is None or isinstance(value, (str, int, float, bool)):
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

    @classmethod
    def _parse_markdown(cls, path: Path) -> tuple[dict[str, Any], str]:
        """Parse front matter and body for a record file."""
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValidationError(f"Cannot read record {path}: {exc}") from exc
        match = _FRONT_MATTER.match(text)
        if match is None:
            raise ValidationError(f"Unterminated YAML front matter in {path}")
        try:
            value = yaml.safe_load(match.group(1))
        except yaml.YAMLError as exc:
            raise ValidationError(
                f"Malformed YAML front matter in {path}: {exc}"
            ) from exc
        if not isinstance(value, Mapping):
            raise ValidationError(f"YAML front matter must be a mapping in {path}")
        return dict(value), text[match.end() :]

    @classmethod
    def _construct_loaded(cls, data: dict[str, Any], markdown: str, path: Path) -> Self:
        # Validation occurs here through model_validate().
        record = cls.model_validate({**data, "markdown": markdown})

        record.path = path
        record._persisted_id = record.id
        return record

    @classmethod
    def load(cls, path: Path | str) -> Self:
        """Load and validate one record from Markdown.

        Args:
            path: Markdown record path.

        Returns:
            A persisted record with its path metadata attached.

        Raises:
            hiraku.errors.RecordNotFoundError: If ``path`` is not a file.
            hiraku.errors.ValidationError: If front matter or model fields are invalid.

        Example:
            ``record = Record.load("hiraku/records/demo/demo.md")``
        """
        path = Path(path).expanduser().absolute()
        if not path.is_file():
            raise RecordNotFoundError(f"{cls.__name__} record not found: {path}")
        data, markdown = cls._parse_markdown(path)
        return cls._construct_loaded(data, markdown, path)

    @classmethod
    def list(cls, paths: Iterable[Path]) -> builtins.list[Self]:
        """Load records from paths in deterministic identifier order.

        Args:
            paths: Iterable of Markdown record paths.

        Returns:
            Validated records sorted by identifier and path.
        """
        records = [cls.load(path) for path in sorted(paths, key=lambda item: str(item))]
        return sorted(records, key=lambda record: (record.id, str(record.path)))

    def save(self, path: Path | str | None = None, *, overwrite: bool = False) -> Path:
        """Persist the record as YAML front matter followed by Markdown.

        Args:
            path: Destination for a detached record. Persisted records reuse their
                original path.
            overwrite: Allow a detached record to replace an existing destination.

        Returns:
            Absolute path of the persisted record.

        Raises:
            hiraku.errors.RecordExistsError: If the destination exists unexpectedly.
            hiraku.errors.ValidationError: If the record is detached without a path
                or cannot be written.

        Example:
            ```python
            from hiraku.project import Project

            project = Project.discover()
            record = Record(id="demo")
            record.save(project.records_dir / "demo" / "demo.md")
            ```
        """
        if self.path is None and path is None:
            raise ValidationError(
                f"Detached {type(self).__name__} {self.id!r} requires a save path"
            )
        target = Path(path).expanduser().absolute() if path is not None else self.path
        assert target is not None
        from .project import Project

        selected = Project.discover(target)
        index = _record_index(selected)
        existing = index.get(self.id)
        if existing is not None and existing.path != target:
            raise RecordExistsError(f"Record {self.id!r} already exists")
        candidate = self.model_copy(deep=True)
        candidate.path = target
        index[self.id] = candidate
        _validate_tree(index, selected.records_dir)
        if self.path is not None and target != self.path:
            raise ValidationError(
                f"Persisted {type(self).__name__} {self.id!r} cannot move from "
                f"{self.path} to {target}"
            )
        same_record = self.path == target
        if target.exists() and not same_record and not overwrite:
            raise RecordExistsError(
                f"{type(self).__name__} record already exists: {target}"
            )
        content = f"---\n{dump_yaml(self.to_dict())}---\n{self.markdown}"
        try:
            atomic_write(target, content)
        except OSError as exc:
            raise ValidationError(f"Cannot save record at {target}: {exc}") from exc
        self.path = target
        self._persisted_id = self.id
        return target

    def save_markdown(self, markdown: Markdown) -> Self:
        """Save a new body and return a saved copy, leaving this object unchanged.

        The record must already have a path. Reject changes to its body or
        metadata on disk since loading, then save through the record API.
        This check does not lock out concurrent writers during the save.

        Args:
            markdown: Replacement Markdown body.

        Returns:
            A saved record of the same subtype with the new body.

        Raises:
            hiraku.errors.ValidationError: If the record is detached, changed
                on disk, or fails validation or persistence.
            hiraku.errors.RecordNotFoundError: If its file no longer exists.
            pydantic.ValidationError: If the replacement body is invalid.
        """
        if self.path is None:
            raise ValidationError("Save this record to a file before editing it.")
        current = type(self).load(self.path)
        if current.to_dict() != self.to_dict() or current.markdown != self.markdown:
            raise ValidationError("This record changed on disk. Reload before saving.")
        current.markdown = markdown
        current.save()
        return current

    def resolve(self, *, project: Any = None) -> ResolvedRecord:
        """Return inherited values and their defining record IDs."""
        selected = _project(project)
        index = _record_index(selected)
        _validate_tree(index, selected.records_dir)
        return _resolve(self, index, set())

    @classmethod
    def create(
        cls,
        record_id: str,
        *,
        parent_id: str | None = None,
        related_to: list[str] | None = None,
        markdown: str = "",
        activate: bool = True,
        project: Any = None,
        **fields: Any,
    ) -> Record:
        """Create a record, materializing inherited metrics."""
        selected = _project(project)
        index = _record_index(selected)
        if record_id in index:
            raise RecordExistsError(f"Record {record_id!r} already exists")
        if parent_id is not None and parent_id not in index:
            raise RecordNotFoundError(f"Parent record {parent_id!r} not found")
        links = related_to or []
        for link in links:
            if link not in index:
                raise RecordNotFoundError(f"Related record {link!r} not found")
        values = dict(fields)
        if parent_id is not None:
            inherited = index[parent_id].resolve(project=selected).values
            for name in _MATERIALIZED_INHERITED_FIELDS:
                if name not in values and name in inherited:
                    values[name] = inherited[name]
        record = cls(
            id=record_id,
            parent_id=parent_id,
            related_to=links,
            markdown=markdown,
            **values,
        )
        root_id = _root_id(parent_id, index) if parent_id else record_id
        record.save(selected.records_dir / root_id / f"{record_id}.md")
        if activate:
            selected.set_active_record(record)
        return record

    def children(self, *, project: Any = None) -> list[Record]:
        return [
            item for item in list_records(project=project) if item.parent_id == self.id
        ]

    def reparent(self, parent_id: str | None, *, project: Any = None) -> None:
        """Change parent and relocate this subtree when its root changes."""
        selected = _project(project)
        index = _record_index(selected)
        _validate_tree(index, selected.records_dir)
        if self.id not in index:
            raise RecordNotFoundError(f"Record {self.id!r} not found")
        if self.path != index[self.id].path:
            raise ValidationError("Reparenting requires the persisted record")
        journal_path = _reparent_journal_path(selected)
        if journal_path.exists():
            raise ValidationError(
                f"A previous reparent operation still needs recovery: {journal_path}"
            )
        if parent_id is not None:
            validate_identifier(parent_id, label="parent ID")
            if parent_id not in index:
                raise RecordNotFoundError(f"Parent record {parent_id!r} not found")
            if parent_id == self.id or _is_descendant(index[parent_id], self.id, index):
                raise ValidationError("A record cannot become its own ancestor")
        old_root = _root_id(self.id, index)
        new_root = _root_id(parent_id, index) if parent_id else self.id
        old_folder = selected.records_dir / old_root
        if old_root == self.id and old_root != new_root:
            record_paths = {item.path for item in index.values()}
            extras = [
                path
                for path in old_folder.rglob("*")
                if path.is_file() and path not in record_paths
            ]
            if extras:
                raise ValidationError(
                    f"Move files in {old_folder} before reparenting its root"
                )
        old_parent_id = self.parent_id
        if old_root == new_root:
            self.parent_id = parent_id
            try:
                self.save()
            except Exception:
                self.parent_id = old_parent_id
                raise
            return
        affected = [
            item
            for item in index.values()
            if item.id == self.id or _is_descendant(item, self.id, index)
        ]
        old_paths = {item.id: item.path for item in affected if item.path is not None}
        if len(old_paths) != len(affected):
            raise ValidationError("Reparenting requires persisted records")
        targets = {
            item.id: selected.records_dir / new_root / f"{item.id}.md"
            for item in affected
        }
        if any(target.exists() for target in targets.values()):
            raise RecordExistsError(f"Destination root folder {new_root!r} conflicts")
        moves = [(old_paths[item.id], targets[item.id]) for item in affected]
        journal = {
            "phase": "preparing",
            "old_root": old_root,
            "new_root": new_root,
            "affected_ids": [item.id for item in affected],
            "moves": [
                {
                    "old": old.relative_to(selected.records_dir).as_posix(),
                    "new": new.relative_to(selected.records_dir).as_posix(),
                }
                for old, new in moves
            ],
        }
        atomic_write(journal_path, json.dumps(journal))
        self.parent_id = parent_id
        try:
            for item in affected:
                target = targets[item.id]
                if item.id == self.id:
                    content = f"---\n{dump_yaml(self.to_dict())}---\n{self.markdown}"
                else:
                    content = old_paths[item.id].read_text(encoding="utf-8")
                atomic_write(target, content)
                if target.read_text(encoding="utf-8") != content:
                    raise OSError(f"Destination copy verification failed for {target}")
                if Record.load(target).id != item.id:
                    raise ValidationError(
                        f"Destination copy has an invalid ID: {target}"
                    )
        except Exception as exc:
            self.parent_id = old_parent_id
            _, cleanup_errors, complete = _recover_reparenting(selected)
            if not complete:
                raise ExceptionGroup(
                    f"Could not write destination records; recovery is pending at "
                    f"{journal_path}",
                    [exc, *cleanup_errors],
                ) from exc
            raise

        journal["phase"] = "committed"
        try:
            atomic_write(journal_path, json.dumps(journal))
        except Exception as exc:
            try:
                persisted_phase = json.loads(
                    journal_path.read_text(encoding="utf-8")
                ).get("phase")
            except (OSError, AttributeError, ValueError):
                persisted_phase = None
            if persisted_phase == "committed":
                self.path = targets[self.id]
                for old_path, _ in moves:
                    with suppress(OSError):
                        old_path.unlink()
                _recover_reparenting(selected)
                with suppress(OSError):
                    old_folder.rmdir()
                return
            self.parent_id = old_parent_id
            _, cleanup_errors, complete = _recover_reparenting(selected)
            if not complete:
                raise ExceptionGroup(
                    f"Could not start reparenting; recovery is pending at "
                    f"{journal_path}",
                    [exc, *cleanup_errors],
                ) from exc
            raise

        for old_path, _ in moves:
            with suppress(OSError):
                old_path.unlink()
        self.path = targets[self.id]
        _recover_reparenting(selected)
        with suppress(OSError):
            old_folder.rmdir()

    def descendants(self, *, project: Any = None) -> list[Record]:
        index = {item.id: item for item in list_records(project=project)}
        return [item for item in index.values() if _is_descendant(item, self.id, index)]

    def tracking_backend_spec(self, *, project: Any = None) -> dict[str, Any]:
        """Return the project-wide tracking backend configuration."""
        selected = _project(project)
        specification = selected.tracking_backend
        if isinstance(specification, str):
            return {"type": specification}
        return dict(specification)

    def resolve_tracking_backend(self, *, project: Any = None) -> TrackingBackend:
        """Create the tracking backend configured for this project."""
        selected = _project(project)
        return create_backend(self.tracking_backend_spec(project=selected), selected)

    def runs(
        self,
        *,
        project: Any = None,
        sort: str | None = None,
        ascending: bool | None = None,
        limit: int | None = None,
    ) -> list[Run]:
        """Return runs attributed to this record.

        Sorting and limiting use the same options as `TrackingBackend.list_runs`.
        """
        return self.resolve_tracking_backend(project=project).list_runs(
            record_id=self.id,
            sort=sort,
            ascending=ascending,
            limit=limit,
        )

    def delete(self, *, project: Any = None) -> None:
        records = list_records(project=project)
        selected = _project(project)
        if _reparent_journal_path(selected).exists():
            raise ValidationError(
                "Cannot delete records until pending reparent recovery is complete"
            )
        persisted = next((item for item in records if item.id == self.id), None)
        if persisted is not None and self.path != persisted.path:
            raise ValidationError("Cannot delete a stale record instance; reload it")
        if any(item.parent_id == self.id for item in records):
            raise ValidationError(f"Cannot delete record {self.id!r} with children")
        if any(self.id in item.related_to for item in records if item.id != self.id):
            raise ValidationError(f"Cannot delete referenced record {self.id!r}")
        if self.path is None:
            raise RecordNotFoundError(f"Record {self.id!r} not found")
        target = self.path
        if not target.exists():
            raise RecordNotFoundError(f"Record not found: {target}")
        try:
            target.unlink()
        except OSError as exc:
            raise ValidationError(f"Cannot delete record at {target}: {exc}") from exc
        self.path = None


def _project(project: Any) -> Any:
    if project is not None:
        return project
    from .project import Project

    return Project.discover()


def _reparent_journal_path(project: Any) -> Path:
    return project.hiraku_dir / ".reparent-transaction.json"


def _recover_reparenting(project: Any) -> tuple[set[Path], list[Exception], bool]:
    """Retry an interrupted reparent and hide stale copies while cleanup is pending."""
    journal_path = _reparent_journal_path(project)
    if not journal_path.exists():
        return set(), [], True
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        phase = journal["phase"]
        raw_moves = journal["moves"]
        old_root = journal["old_root"]
        new_root = journal["new_root"]
        affected_ids = journal["affected_ids"]
        if (
            phase not in {"preparing", "committed"}
            or not isinstance(raw_moves, list)
            or not isinstance(old_root, str)
            or not isinstance(new_root, str)
            or not isinstance(affected_ids, list)
            or any(not isinstance(record_id, str) for record_id in affected_ids)
            or len(raw_moves) != len(affected_ids)
        ):
            raise ValueError("invalid journal structure")
        moves = []
        for record_id, item in zip(affected_ids, raw_moves, strict=True):
            old = Path(item["old"])
            new = Path(item["new"])
            if (
                old.is_absolute()
                or new.is_absolute()
                or ".." in old.parts
                or ".." in new.parts
            ):
                raise ValueError("journal paths must stay inside the records folder")
            if (
                old.parent != Path(old_root)
                or new.parent != Path(new_root)
                or old.name != f"{record_id}.md"
                or new.name != f"{record_id}.md"
            ):
                raise ValueError("journal paths do not match the affected records")
            moves.append((project.records_dir / old, project.records_dir / new))
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ValidationError(
            f"Cannot read reparent recovery journal {journal_path}: {exc}"
        ) from exc

    ignored_paths: set[Path] = set()
    cleanup_errors: list[Exception] = []
    if phase == "preparing":
        missing_old = [old for old, _ in moves if not old.exists()]
        if missing_old:
            missing = ", ".join(str(path) for path in missing_old)
            raise ValidationError(
                f"Authoritative pre-commit copy is missing: {missing}"
            )
        ignored_paths.update(new for _, new in moves)
        for _, new in moves:
            try:
                new.unlink(missing_ok=True)
            except OSError as exc:
                cleanup_errors.append(
                    OSError(
                        f"Could not remove incomplete destination copy {new}: {exc}"
                    )
                )
        complete = all(not new.exists() for _, new in moves)
    else:
        missing_new = [new for _, new in moves if not new.exists()]
        if missing_new:
            missing = ", ".join(str(path) for path in missing_new)
            raise ValidationError(f"Authoritative committed copy is missing: {missing}")
        ignored_paths.update(old for old, _ in moves)
        for old, _ in moves:
            unlink_error: OSError | None = None
            try:
                old.unlink(missing_ok=True)
            except OSError as exc:
                unlink_error = exc
            if old.exists():
                detail = f": {unlink_error}" if unlink_error else ""
                cleanup_errors.append(
                    OSError(f"Could not remove old record copy {old}{detail}")
                )
        complete = all(not old.exists() and new.exists() for old, new in moves)

    if complete:
        try:
            journal_path.unlink()
        except OSError as exc:
            cleanup_errors.append(
                OSError(
                    f"Could not clear reparent recovery journal {journal_path}: {exc}"
                )
            )
            complete = False
    return ignored_paths, cleanup_errors, complete


def _record_index(project: Any) -> dict[str, Record]:
    ignored_paths, _, _ = _recover_reparenting(project)
    index: dict[str, Record] = {}
    for path in sorted(project.records_dir.glob("*/*.md")):
        if path in ignored_paths:
            continue
        record = Record.load(path)
        if record.id in index:
            raise ValidationError(f"Duplicate record ID {record.id!r}")
        index[record.id] = record
    return index


def _root_id(identifier: str, index: Mapping[str, Record]) -> str:
    seen: set[str] = set()
    while True:
        if identifier in seen:
            raise ValidationError(f"Record parent cycle at {identifier!r}")
        seen.add(identifier)
        try:
            record = index[identifier]
        except KeyError as exc:
            raise RecordNotFoundError(
                f"Parent record {identifier!r} not found"
            ) from exc
        if record.parent_id is None:
            return identifier
        identifier = record.parent_id


def _validate_tree(index: Mapping[str, Record], records_dir: Path) -> None:
    for record in index.values():
        root = _root_id(record.id, index)
        expected = records_dir / root / f"{record.id}.md"
        if record.path != expected:
            raise ValidationError(f"Record {record.id!r} is outside its root folder")
        for link in record.related_to:
            if link not in index:
                raise RecordNotFoundError(f"Related record {link!r} not found")


def _resolve(
    record: Record, index: Mapping[str, Record], seen: set[str]
) -> ResolvedRecord:

    if record.id in seen:
        raise ValidationError(f"Record parent cycle at {record.id!r}")
    seen.add(record.id)
    values: dict[str, Any] = {}
    sources: dict[str, Any] = {}
    if record.parent_id is not None:
        parent = _resolve(index[record.parent_id], index, seen)
        values = {
            key: value
            for key, value in parent.values.items()
            if key not in _LOCAL_FIELDS
        }
        sources = {
            key: value
            for key, value in parent.sources.items()
            if key not in _LOCAL_FIELDS
        }
    for key, value in record.to_dict().items():
        if key == "metrics" and isinstance(value, list):
            values[key] = [Metric.model_validate(item) for item in value]
            sources[key] = record.id
        else:
            values[key] = value
            sources[key] = record.id
    return ResolvedRecord(values, sources)


def _is_descendant(
    record: Record, ancestor_id: str, index: Mapping[str, Record]
) -> bool:
    seen: set[str] = set()
    parent_id = record.parent_id
    while parent_id is not None:
        if parent_id in seen:
            raise ValidationError(f"Record parent cycle at {parent_id!r}")
        if parent_id == ancestor_id:
            return True
        seen.add(parent_id)
        parent_id = index[parent_id].parent_id
    return False


def load_record(record_id: str, *, project: Any = None) -> Record:
    validate_identifier(record_id, label="record ID")
    selected = _project(project)
    index = _record_index(selected)
    _validate_tree(index, selected.records_dir)
    try:
        return index[record_id]
    except KeyError as exc:
        raise RecordNotFoundError(f"Record {record_id!r} not found") from exc


def list_records(*, project: Any = None) -> list[Record]:
    selected = _project(project)
    index = _record_index(selected)
    _validate_tree(index, selected.records_dir)
    return [index[key] for key in sorted(index)]
