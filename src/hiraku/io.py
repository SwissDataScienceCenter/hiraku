"""Safe YAML and atomic-file helpers used by Hiraku's storage modules."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from .errors import ValidationError


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    """
    Load a YAML mapping from *path*.

    Args:
        path: The path to the YAML file.

    Returns:
        The loaded YAML mapping.

    Raises:
        ValidationError: If the file cannot be read or is not a valid YAML mapping.

    Examples:
        >>> from tempfile import TemporaryDirectory
        >>> with TemporaryDirectory() as directory:
        ...     path = Path(directory) / "example.yml"
        ...     _ = path.write_text("key: value\\n", encoding="utf-8")
        ...     load_yaml_mapping(path)
        {'key': 'value'}
        >>> with TemporaryDirectory() as directory:
        ...     path = Path(directory) / "empty.yml"
        ...     _ = path.write_text("", encoding="utf-8")
        ...     load_yaml_mapping(path)
        {}
        >>> load_yaml_mapping(Path("missing.yml"))
        Traceback (most recent call last):
        ...
        hiraku.errors.ValidationError: Cannot read YAML mapping at missing.yml: ...
    """
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValidationError(f"Cannot read YAML mapping at {path}: {exc}") from exc
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValidationError(f"Expected a YAML mapping at {path}")
    return dict(value)


def dump_yaml(data: Mapping[str, Any]) -> str:
    """
    Serialize a mapping as deterministic, human-editable YAML.

    Args:
        data: The mapping to serialize.

    Returns:
        The serialized YAML string.

    Raises:
        ValidationError: If the data cannot be serialized as YAML.

    Examples:
        >>> dump_yaml({"key": "value"})
        'key: value\\n'
        >>> dump_yaml({"list": [1, 2, 3]})
        'list:\\n- 1\\n- 2\\n- 3\\n'
        >>> dump_yaml({"nested": {"a": 1, "b": 2}})
        'nested:\\n  a: 1\\n  b: 2\\n'
        >>> dump_yaml({"unicode": "✓"})
        'unicode: ✓\\n'
        >>> dump_yaml({"invalid": object()})
        Traceback (most recent call last):
        ...
        hiraku.errors.ValidationError: Unsupported YAML value: ...
    """
    try:
        return yaml.safe_dump(
            dict(data), sort_keys=False, allow_unicode=True, default_flow_style=False
        )
    except yaml.YAMLError as exc:
        raise ValidationError(f"Unsupported YAML value: {exc}") from exc


def atomic_write(path: Path, content: str, *, mode: int | None = None) -> None:
    """
    Atomically replace *path* with UTF-8 *content*.

    Args:
        path: The path to the file to be replaced.
        content: The new content to write to the file.
        mode: The file mode to set on the new file.

    Raises:
        OSError: If the file cannot be written.

    Examples:
        >>> from tempfile import TemporaryDirectory
        >>> with TemporaryDirectory() as directory:
        ...     path = Path(directory) / "example.txt"
        ...     atomic_write(path, "Hello, world!\\n")
        ...     path.read_text(encoding="utf-8")
        'Hello, world!\\n'
        >>> with TemporaryDirectory() as directory:
        ...     path = Path(directory) / "example.txt"
        ...     _ = path.write_text("Old content\\n", encoding="utf-8")
        ...     atomic_write(path, "New content\\n")
        ...     path.read_text(encoding="utf-8")
        'New content\\n'
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            temporary.chmod(mode)
        os.replace(temporary, path)
    except OSError:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
