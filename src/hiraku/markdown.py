"""Markdown section parsing for record bodies."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .errors import ValidationError

_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_PUNCTUATION = re.compile(r"[^\w\s-]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


def normalize_anchor(heading: str) -> str:
    """Return the anchor used to reference a Markdown heading.

    Lowercases the heading text, removes punctuation, and replaces runs of
    whitespace with single hyphens.

    Example:
        ``normalize_anchor("Approve the Launch!") == "approve-the-launch"``
    """
    text = _PUNCTUATION.sub("", heading.strip().lower())
    return _WHITESPACE.sub("-", text.strip())


@dataclass(frozen=True)
class Section:
    """Location of one heading's section within a Markdown body."""

    level: int
    start: int
    end: int


@dataclass(frozen=True)
class ParsedMarkdown:
    """Lines, sections by anchor, and orientation boundary of a Markdown body."""

    lines: list[str]
    sections: dict[str, Section]
    orientation_end: int


def parse_markdown(markdown: str, *, label: str) -> ParsedMarkdown:
    """Index the sections of a workflow's Markdown body.

    Headings inside fenced code blocks are ignored. A section runs to the next
    heading of equal or higher level. The orientation ends at the first
    second-level heading.

    Raises:
        hiraku.errors.ValidationError: If two headings share a normalized anchor.
    """
    lines = markdown.splitlines(keepends=True)
    sections: dict[str, Section] = {}
    orientation_end = len(lines)
    headings: list[tuple[int, int, str]] = []
    fence: str | None = None
    for index, line in enumerate(lines):
        fence_match = _FENCE.match(line)
        if fence is not None:
            if fence_match and fence_match.group(1).startswith(fence):
                fence = None
            continue
        if fence_match:
            fence = fence_match.group(1)
            continue
        heading = _HEADING.match(line.rstrip("\r\n"))
        if heading:
            headings.append((index, len(heading.group(1)), heading.group(2) or ""))
    for position, (index, level, title) in enumerate(headings):
        if level == 2 and orientation_end == len(lines):
            orientation_end = index
        anchor = normalize_anchor(title)
        if not anchor:
            continue
        if anchor in sections:
            raise ValidationError(
                f"Headings in {label} share the anchor #{anchor}; rename one"
            )
        end = next(
            (
                following
                for following, following_level, _ in headings[position + 1 :]
                if following_level <= level
            ),
            len(lines),
        )
        sections[anchor] = Section(level, index, end)
    return ParsedMarkdown(lines, sections, orientation_end)


_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def placeholders(markdown: str) -> set[str]:
    """Return the names of the ``{{name}}`` placeholders in a Markdown text."""
    return set(_PLACEHOLDER.findall(markdown))


def render(markdown: str, values: Mapping[str, Any]) -> str:
    """Replace each ``{{name}}`` placeholder with its value.

    Booleans render as ``true`` and ``false``, ``None`` as an empty string,
    and other values with ``str``.

    Raises:
        hiraku.errors.ValidationError: If a placeholder has no value.
    """
    missing = sorted(placeholders(markdown) - set(values))
    if missing:
        raise ValidationError(f"No value for placeholders: {', '.join(missing)}")

    def replace(match: re.Match[str]) -> str:
        value = values[match.group(1)]
        if isinstance(value, bool):
            return "true" if value else "false"
        return "" if value is None else str(value)

    return _PLACEHOLDER.sub(replace, markdown)
