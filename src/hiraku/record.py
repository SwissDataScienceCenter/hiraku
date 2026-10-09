"""Research records, entrypoints, and metric declarations."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .base import BaseRecord
from .errors import RecordExistsError, RecordNotFoundError, ValidationError
from .io import atomic_write, dump_yaml
from .run import Run
from .tracking import TrackingBackend, create_backend
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

    def resolve_tracking_backend(self, *, config: Any = None) -> TrackingBackend:
        """Create the tracking backend inherited by this record."""
        selected = _config(config)
        specification = self.resolve(config=selected).values.get("tracking_backend")
        return create_backend(specification or selected.tracking_backend, selected)

    def runs(self, *, config: Any = None, **query: Any) -> list[Run]:
        """Return runs attributed to this record."""
        return self.resolve_tracking_backend(config=config).list_runs(
            record_id=self.id, **query
        )

    def delete(self, *, config: Any = None) -> None:
        records = list_records(config=config)
        selected = _config(config)
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
        super().delete()


def _config(config: Any) -> Any:
    if config is not None:
        return config
    from .config import HirakuConfig

    return HirakuConfig.discover()


def _reparent_journal_path(config: Any) -> Path:
    return config.hiraku_dir / ".reparent-transaction.json"


def _recover_reparenting(config: Any) -> tuple[set[Path], list[Exception], bool]:
    """Retry an interrupted reparent and hide stale copies while cleanup is pending."""
    journal_path = _reparent_journal_path(config)
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
            moves.append((config.records_dir / old, config.records_dir / new))
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


def _record_index(config: Any) -> dict[str, Record]:
    ignored_paths, _, _ = _recover_reparenting(config)
    index: dict[str, Record] = {}
    for path in sorted(config.records_dir.glob("*/*.md")):
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
