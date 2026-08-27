"""YAML and front-matter I/O.

PyYAML is used when importable; a fallback parser covers the front-matter subset documented in
docs/autoresearch/data-model.md so the tool works in an environment with no dependencies at all.
Serialisation is always ours, so key order is stable and diffs stay small.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

from .errors import HirakuError

try:  # pragma: no cover - optional dependency
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def _strip_comment(raw: str) -> str:
    """Drop a trailing ` # comment`, respecting quotes.

    A cap written as `max_bytes_per_file: 60000   # why` must parse as 60000, not as a string —
    otherwise the limit silently stops being a number on an install without PyYAML.
    """
    out: list[str] = []
    quote = ""
    for index, char in enumerate(raw):
        if quote:
            out.append(char)
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
            out.append(char)
            continue
        if char == "#" and (index == 0 or raw[index - 1] in " \t"):
            break
        out.append(char)
    return "".join(out)


def _scalar(raw: str) -> Any:
    text = _strip_comment(raw).strip()
    if not text:
        return None
    if len(text) > 1 and text[0] in "\"'" and text[-1] == text[0]:
        return text[1:-1]
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [_scalar(p) for p in inner.split(",")] if inner else []
    low = text.lower()
    if low in {"true", "yes"}:
        return True
    if low in {"false", "no"}:
        return False
    if low in {"null", "none", "~"}:
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _child_is_a_list(lines: list[str], index: int, indent: int) -> bool:
    """Look ahead from a `key:` line: does a block sequence follow, or a nested mapping?

    YAML allows a sequence's items at the key's own indent as well as deeper, so the indent alone
    cannot decide it.
    """
    for line in lines[index + 1 :]:
        child_indent = len(line) - len(line.lstrip())
        if child_indent > indent:
            return line.strip().startswith("- ")
        if child_indent == indent:
            return line.strip().startswith("- ")
        return False
    return False


def _fallback_load(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]
    lines = [
        line
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    for index, line in enumerate(lines):
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        item = stripped.startswith("- ")
        while len(stack) > 1:
            depth, container = stack[-1]
            if indent > depth:
                break
            # A sequence item at its key's own indent still belongs to that sequence.
            if indent == depth and item and isinstance(container, list):
                break
            stack.pop()
        container = stack[-1][1]
        if item:
            if isinstance(container, list):
                container.append(_scalar(stripped[2:]))
            continue
        if ":" not in stripped or not isinstance(container, dict):
            continue
        key, _, rest = stripped.partition(":")
        key, rest = key.strip(), rest.strip()
        if rest == "":
            child: Any = [] if _child_is_a_list(lines, index, indent) else {}
            container[key] = child
            stack.append((indent, child))
        else:
            container[key] = _scalar(rest)
    return {k: (None if v == {} else v) for k, v in root.items()}


def load(text: str) -> dict[str, Any]:
    """Parse a YAML mapping. Returns {} for empty or non-mapping documents."""
    if yaml is not None:
        try:
            data = yaml.safe_load(text)
        except Exception:  # malformed under PyYAML: try the permissive parser
            return _fallback_load(text)
        return data if isinstance(data, dict) else {}
    return _fallback_load(text)


def load_file(path: Path) -> dict[str, Any]:
    try:
        return load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise HirakuError(f"Could not read {path}: {exc}") from exc


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()
    if isinstance(value, list):
        return "[" + ", ".join(_fmt(v) for v in value) + "]"
    text = str(value)
    # Quote only when the value would otherwise be re-read as something else.
    if text != text.strip() or text.lower() in {"true", "false", "null", "yes", "no"}:
        return f'"{text}"'
    if ": " in text or text.startswith(("[", "{", "#", "&", "*", "!", "|", ">", "%", "@")):
        return '"' + text.replace('"', '\\"') + '"'
    if text.endswith(":"):
        return f'"{text}"'
    return text


def dump(data: dict[str, Any], *, indent: int = 0) -> str:
    """Serialise a mapping. Insertion order is preserved, so records keep a stable field order."""
    lines: list[str] = []
    pad = " " * indent
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{pad}{key}:")
            lines.append(dump(value, indent=indent + 2).rstrip("\n"))
        else:
            rendered = _fmt(value)
            lines.append(f"{pad}{key}:" + (f" {rendered}" if rendered else ""))
    return "\n".join(lines) + "\n"


def split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """Return (front matter, body). A document without front matter yields ({}, text)."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            front = load("".join(lines[1:index]))
            body = "".join(lines[index + 1 :])
            return front, body.lstrip("\n")
    raise HirakuError("Unterminated YAML front matter.")


def join_front_matter(front: dict[str, Any], body: str) -> str:
    return "---\n" + dump(front) + "---\n\n" + body.lstrip("\n")
