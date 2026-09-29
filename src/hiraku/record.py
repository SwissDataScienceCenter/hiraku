"""Research records, entrypoints, and metric declarations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .base import BaseRecord
from .errors import RecordExistsError, RecordNotFoundError, ValidationError
from .io import atomic_write, dump_yaml
from .validation import validate_identifier

ArgumentScalar: TypeAlias = str | int | float | Path
ArgumentValue: TypeAlias = ArgumentScalar | bool | None | Sequence[ArgumentScalar]


class Entrypoint(BaseModel):
    """A command and default named arguments."""

    model_config = ConfigDict(extra="forbid")
    command: list[str]
    args: dict[str, ArgumentValue] = Field(default_factory=dict)

    @field_validator("args", mode="before")
    @classmethod
    def validate_args_mapping(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            raise ValueError("Entrypoint args must be a mapping")
        return value

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: list[str]) -> list[str]:
        if not value or any(not item for item in value):
            raise ValidationError("Entrypoint command must be non-empty strings")
        return value


class Metric(BaseModel):
    """A declared metric and its preferred direction."""

    name: str = Field(min_length=1)
    direction: Literal["minimize", "maximize"]


_LOCAL_FIELDS = frozenset(
    {"id", "parent_id", "related_to", "type", "commit", "status", "decision"}
)


@dataclass(frozen=True)
class ResolvedRecord:
    values: dict[str, Any]
    sources: dict[str, Any]


class Record(BaseRecord):
    """A research record with one organizational parent and optional links."""

    parent_id: str | None = None
    related_to: list[str] = Field(default_factory=list)
    type: str | None = None
    commit: str | None = None
    status: str | None = None
    decision: str | None = None
    branch: str | None = None
    entrypoints: dict[str, Any] | None = None
    metrics: list[Any] | None = None
    tracking_backend: str | dict[str, Any] | None = None

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
        if self.entrypoints is not None:
            
            for name in self.entrypoints:
                validate_identifier(name, label="entrypoint name")
            self.__dict__["entrypoints"] = {
                name: Entrypoint.model_validate(value)
                for name, value in self.entrypoints.items()
            }
        if self.metrics is not None:
            
            self.__dict__["metrics"] = [
                Metric.model_validate(item) for item in self.metrics
            ]
        return self

    def save(self, path: Path | str | None = None, *, overwrite: bool = False) -> Path:
        """Persist only when project references and root placement are valid."""
        from .config import HirakuConfig

        target = Path(path).expanduser().absolute() if path is not None else self.path
        if target is None:
            raise ValidationError(f"Detached record {self.id!r} requires a save path")
        selected = HirakuConfig.discover(target)
        index = _record_index(selected)
        existing = index.get(self.id)
        if existing is not None and existing.path != target:
            raise RecordExistsError(f"Record {self.id!r} already exists")
        candidate = self.model_copy(deep=True)
        candidate.path = target
        index[self.id] = candidate
        _validate_tree(index, selected.records_dir)
        return super().save(target, overwrite=overwrite)

    def resolve(self, *, config: Any = None) -> ResolvedRecord:
        """Return inherited values and their defining record IDs."""
        selected = _config(config)
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
        config: Any = None,
        **fields: Any,
    ) -> Record:
        selected = _config(config)
        index = _record_index(selected)
        if record_id in index:
            raise RecordExistsError(f"Record {record_id!r} already exists")
        if parent_id is not None and parent_id not in index:
            raise RecordNotFoundError(f"Parent record {parent_id!r} not found")
        links = related_to or []
        for link in links:
            if link not in index:
                raise RecordNotFoundError(f"Related record {link!r} not found")
        record = cls(
            id=record_id,
            parent_id=parent_id,
            related_to=links,
            markdown=markdown,
            **fields,
        )
        root_id = _root_id(parent_id, index) if parent_id else record_id
        record.save(selected.records_dir / root_id / f"{record_id}.md")
        if activate:
            selected.set_active_record(record)
        return record

    def children(self, *, config: Any = None) -> list[Record]:
        return [
            item for item in list_records(config=config) if item.parent_id == self.id
        ]

    def reparent(self, parent_id: str | None, *, config: Any = None) -> None:
        """Change parent and relocate this subtree when its root changes."""
        selected = _config(config)
        index = _record_index(selected)
        _validate_tree(index, selected.records_dir)
        if self.id not in index:
            raise RecordNotFoundError(f"Record {self.id!r} not found")
        if self.path != index[self.id].path:
            raise ValidationError("Reparenting requires the persisted record")
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
        self.parent_id = parent_id
        if old_root == new_root:
            self.save()
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
        written: list[Path] = []
        try:
            for item in affected:
                target = targets[item.id]
                if item.id == self.id:
                    content = f"---\n{dump_yaml(self.to_dict())}---\n{self.markdown}"
                else:
                    content = old_paths[item.id].read_text(encoding="utf-8")
                atomic_write(target, content)
                written.append(target)
        except Exception:
            for target in written:
                target.unlink(missing_ok=True)
            raise

        # Keep a list of removed paths to attempt rollback
        # if any error occurs during unlinking.
        removed: list[Path] = []
        try:
            for item in affected:
                old_path = old_paths[item.id]
                old_path.unlink()
                removed.append(old_path)
        except Exception as exc:
            rollback_errors: list[Exception] = []
            for old_path in removed:
                target = targets[old_path.stem]
                try:
                    atomic_write(old_path, target.read_text(encoding="utf-8"))
                except Exception as rollback_exc:
                    rollback_errors.append(rollback_exc)
            for target in written:
                try:
                    target.unlink(missing_ok=True)
                except Exception as rollback_exc:
                    rollback_errors.append(rollback_exc)
            if rollback_errors:
                raise ExceptionGroup(
                    "Reparenting failed and rollback was incomplete",
                    [exc, *rollback_errors],
                ) from exc
            raise
        self.path = targets[self.id]
        with suppress(OSError):
            old_folder.rmdir()

    def descendants(self, *, config: Any = None) -> list[Record]:
        index = {item.id: item for item in list_records(config=config)}
        return [item for item in index.values() if _is_descendant(item, self.id, index)]

    def entrypoint(self, name: str, *, config: Any = None) -> Any:
        from .errors import EntrypointNotFoundError

        entrypoints = self.resolve(config=config).values.get("entrypoints") or {}
        try:
            return entrypoints[name]
        except KeyError as exc:
            raise EntrypointNotFoundError(
                f"Record {self.id!r} has no entrypoint {name!r}"
            ) from exc

    def runs(
        self,
        *,
        backend: Any = None,
        include_descendants: bool = False,
        include_unattributed: bool = False,
        config: Any = None,
        **query: Any,
    ) -> list[Any]:
        from .tracking import TrackingBackend, list_runs_for_records

        selected = _config(config) if config is not None or backend is None else None
        records = [self]
        if include_descendants:
            assert selected is not None
            records.extend(self.descendants(config=selected))
        if backend is not None:
            if include_descendants:
                runs = backend.list_runs_for_record_ids(
                    record_ids={record.id for record in records},
                    include_unattributed=include_unattributed,
                )
            else:
                runs = backend.list_all_recorded_runs(
                    include_unattributed=include_unattributed
                )
        else:
            assert selected is not None
            runs = list_runs_for_records(
                records,
                selected,
                include_descendants=include_descendants,
                include_unattributed=include_unattributed,
            )
        return TrackingBackend._apply_query(runs, query, "tracking")

    def delete(self, *, config: Any = None) -> None:
        records = list_records(config=config)
        if any(item.parent_id == self.id for item in records):
            raise ValidationError(f"Cannot delete record {self.id!r} with children")
        if any(self.id in item.related_to for item in records if item.id != self.id):
            raise ValidationError(f"Cannot delete referenced record {self.id!r}")
        super().delete()


def _config(config: Any) -> Any:
    if config is not None:
        return config
    from .config import HirakuConfig

    return HirakuConfig.discover()


def _record_index(config: Any) -> dict[str, Record]:
    index: dict[str, Record] = {}
    for path in sorted(config.records_dir.glob("*/*.md")):
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
        if key == "entrypoints" and isinstance(value, dict):
            values[key] = {
                **(values.get(key) or {}),
                **{
                    name: Entrypoint.model_validate(item)
                    for name, item in value.items()
                },
            }
            sources[key] = {
                **(sources.get(key) or {}),
                **dict.fromkeys(value, record.id),
            }
        elif key == "metrics" and isinstance(value, list):
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


def load_record(record_id: str, *, config: Any = None) -> Record:
    validate_identifier(record_id, label="record ID")
    selected = _config(config)
    index = _record_index(selected)
    _validate_tree(index, selected.records_dir)
    try:
        return index[record_id]
    except KeyError as exc:
        raise RecordNotFoundError(f"Record {record_id!r} not found") from exc


def list_records(*, config: Any = None) -> list[Record]:
    selected = _config(config)
    index = _record_index(selected)
    _validate_tree(index, selected.records_dir)
    return [index[key] for key in sorted(index)]
