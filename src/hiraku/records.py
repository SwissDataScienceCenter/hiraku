"""Mapping-like Markdown metadata records."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping, MutableMapping
from pathlib import Path
from typing import Any, ClassVar, Self

import yaml

from .errors import RecordExistsError, RecordNotFoundError, ValidationError
from .io import atomic_write, dump_yaml

_SAFE_ID = re.compile(r'^[^<>:"/\\|?*\x00-\x1f\x7f]+$')
_FRONT_MATTER = re.compile(
    r"\A---\r?\n(.*?)^---[ \t]*(?:\r?\n|\Z)", re.MULTILINE | re.DOTALL
)


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


def _validate_yaml_value(value: Any, path: str = "field") -> None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    if isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _validate_yaml_value(item, f"{path}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValidationError(f"Unsupported non-string YAML key in {path}")
            _validate_yaml_value(item, f"{path}.{key}")
        return
    raise ValidationError(f"Unsupported YAML value in {path}: {type(value).__name__}")


def _parse_markdown(path: Path) -> tuple[dict[str, Any], str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValidationError(f"Cannot read record {path}: {exc}") from exc
    match = _FRONT_MATTER.match(text)
    if match is None:
        raise ValidationError(f"Unterminated YAML front matter in {path}")
    front_matter = match.group(1)
    try:
        value = yaml.safe_load(front_matter)
    except yaml.YAMLError as exc:
        raise ValidationError(f"Malformed YAML front matter in {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ValidationError(f"YAML front matter must be a mapping in {path}")
    data = dict(value)
    _validate_yaml_value(data)
    return data, text[match.end() :]


class Record(MutableMapping[str, Any]):
    """Base class for a detached or persisted Markdown record."""

    _core_fields: ClassVar[set[str]] = {"id"}

    def __init__(self, record_id: str, *, markdown: str = "", **fields: Any) -> None:
        object.__setattr__(self, "_data", {"id": record_id, **fields})
        object.__setattr__(self, "markdown", markdown)
        object.__setattr__(self, "path", None)
        object.__setattr__(self, "_persisted_id", None)

    @property
    def id(self) -> str:
        return self._data["id"]

    @id.setter
    def id(self, value: str) -> None:
        if self._persisted_id is not None and value != self._persisted_id:
            raise ValidationError(
                f"Record ID is immutable after persistence: {self.id}"
            )
        self._data["id"] = value

    def __getattr__(self, name: str) -> Any:
        if name in self._core_fields:
            return self._data.get(name)
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in self._core_fields:
            if name == "id":
                type(self).id.fset(self, value)  # type: ignore[union-attr]
            else:
                self._data[name] = value
        else:
            object.__setattr__(self, name, value)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        if key == "id":
            self.id = value
        else:
            self._data[key] = value

    def __delitem__(self, key: str) -> None:
        if key == "id":
            raise ValidationError("A record ID cannot be deleted")
        del self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)

    def validate(self) -> None:
        validate_identifier(self.id)
        if not isinstance(self.markdown, str):
            raise ValidationError(f"Markdown body for {self.id!r} must be a string")
        _validate_yaml_value(self._data)

    @classmethod
    def _construct_loaded(cls, data: dict[str, Any], markdown: str, path: Path) -> Self:
        identifier = data.pop("id", None)
        record = cls(identifier, markdown=markdown, **data)
        object.__setattr__(record, "path", path)
        object.__setattr__(record, "_persisted_id", record.id)
        record.validate()
        return record

    @classmethod
    def load(cls, path: Path | str) -> Self:
        """Load one record from an explicit Markdown file path."""
        path = Path(path).expanduser().absolute()
        if not path.is_file():
            raise RecordNotFoundError(f"{cls.__name__} record not found: {path}")
        data, markdown = _parse_markdown(path)
        return cls._construct_loaded(data, markdown, path)

    @classmethod
    def list(cls, paths: Iterable[Path]) -> list[Self]:
        """Load records from explicit paths in deterministic ID order."""
        records = [cls.load(path) for path in sorted(paths, key=lambda item: str(item))]
        return sorted(records, key=lambda record: (record.id, str(record.path)))

    def save(self, path: Path | str | None = None, *, overwrite: bool = False) -> Path:
        """Persist the record, accepting a path only for its first save."""
        self.validate()
        if self.path is None and path is None:
            raise ValidationError(
                f"Detached {type(self).__name__} {self.id!r} requires a save path"
            )
        target = Path(path).expanduser().absolute() if path is not None else self.path
        assert target is not None
        if self.path is not None and target != self.path:
            raise ValidationError(
                f"Persisted {type(self).__name__} {self.id!r} cannot move from "
                f"{self.path} to {target}"
            )
        if self._persisted_id is not None and self.id != self._persisted_id:
            raise ValidationError(
                f"Record ID is immutable after persistence: {self._persisted_id}"
            )
        same_record = self.path == target
        if target.exists() and not same_record and not overwrite:
            raise RecordExistsError(
                f"{type(self).__name__} record already exists: {target}"
            )
        content = f"---\n{dump_yaml(self._data)}---\n{self.markdown}"
        try:
            atomic_write(target, content)
        except OSError as exc:
            raise ValidationError(f"Cannot save record at {target}: {exc}") from exc
        object.__setattr__(self, "path", target)
        object.__setattr__(self, "_persisted_id", self.id)
        return target

    def delete(self) -> None:
        if self.path is None:
            raise ValidationError(
                f"Detached {type(self).__name__} {self.id!r} has no path to delete"
            )
        target = self.path
        if not target.exists():
            raise RecordNotFoundError(
                f"{type(self).__name__} record not found: {target}"
            )
        try:
            target.unlink()
        except OSError as exc:
            raise ValidationError(f"Cannot delete record at {target}: {exc}") from exc
        object.__setattr__(self, "path", None)
