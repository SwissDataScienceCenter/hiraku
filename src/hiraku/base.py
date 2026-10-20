"""Pydantic-backed Markdown metadata records."""

from __future__ import annotations

import builtins
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Self

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
from .validation import validate_identifier

_FRONT_MATTER = re.compile(
    r"\A---\r?\n(.*?)^---[ \t]*(?:\r?\n|\Z)", re.MULTILINE | re.DOTALL
)


class BaseRecord(BaseModel):
    """Base class for detached or persisted Markdown records.

    Attributes:
        id: Stable identifier used in the record path.
        markdown: Free-form Markdown body; a `Markdown` string, whose sections
            can be selected by heading, e.g. ``record.markdown.section("## Change")``.
        path: Persistence path, populated after saving or loading.

    Example:
        ``BaseRecord(id="demo", markdown="## Objective\\nMeasure accuracy.")``

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

    id: str = Field(min_length=1)
    markdown: Markdown = Markdown("")

    # Persistence metadata (not part of the record's data)
    path: Path | None = Field(default=None, exclude=True)
    _persisted_id: str | None = PrivateAttr(default=None)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise TypeError("record ID must be a string")
        validate_identifier(value, label="record ID")
        return value

    @field_validator("markdown", mode="before")
    @classmethod
    def validate_markdown(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise TypeError("Markdown body must be a string")
        return value

    @model_validator(mode="after")
    def validate_yaml_fields(self) -> Self:
        type(self)._validate_yaml_value(self.to_dict())
        return self

    def __setattr__(self, name: str, value: Any) -> None:
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
            record = BaseRecord(id="demo")
            record.save("demo.md")
            ```
        """
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

    def delete(self) -> None:
        """Delete this persisted record from disk.

        Raises:
            hiraku.errors.ValidationError: If the record is detached or deletion fails.
            hiraku.errors.RecordNotFoundError: If the persisted file is missing.
        """
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
        self.path = None
