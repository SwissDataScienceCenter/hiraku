"""Markdown bodies: section selection by heading and placeholder rendering."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from markdown_it import MarkdownIt
from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema

from .errors import SectionNotFoundError, ValidationError

_PARSER = MarkdownIt("commonmark")
# A heading line; like in Markdown, a closing sequence of # is not part of it.
_SELECTOR = re.compile(r"^(#{1,6})[ \t]+(\S.*?)(?:[ \t]+#+)?$")


@dataclass(frozen=True)
class Heading:
    """A top-level heading of a Markdown body and the lines of its section.

    Attributes:
        level: Heading level, 1 to 6, for both ``#`` and underlined headings.
        title: Heading text as written, with runs of whitespace collapsed.
        start: Index of the heading's first line.
        end: Index of the first line after the section; the section includes
            its subsections.

    Examples:
        >>> heading = Heading(level=2, title="Step", start=2, end=4)
        >>> str(heading)
        '## Step'
    """

    level: int
    title: str
    start: int
    end: int

    def __str__(self) -> str:
        return f"{'#' * self.level} {self.title}"


class Markdown(str):
    """A Markdown body: a string that can select its sections by heading.

    Headings are found with a CommonMark parser, so headings in code blocks,
    lists, and block quotes do not start sections. Sections are returned as
    written in the source.

    Examples:
        >>> markdown = Markdown("# Intro\\n\\n## Step\\nDo it.")
        >>> markdown.section("## Step")
        '## Step\\nDo it.'
        >>> [(heading.level, heading.title) for heading in markdown.headings]
        [(1, 'Intro'), (2, 'Step')]
    """

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.no_info_after_validator_function(
            cls,
            core_schema.str_schema(),
            serialization=core_schema.plain_serializer_function_ser_schema(str),
        )

    @cached_property
    def _lines(self) -> list[str]:
        # Count lines as markdown-it does: it treats \r\n and \r as \n, while
        # str.splitlines would also split at characters such as \u2028.
        return self.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    @cached_property
    def headings(self) -> tuple[Heading, ...]:
        """Return the headings that start sections, in document order.

        Examples:
            >>> Markdown("# Intro\\n\\n## Step").headings[1].title
            'Step'
        """
        tokens = _PARSER.parse(str(self))
        found: list[tuple[int, str, int]] = []
        for index, token in enumerate(tokens):
            # Nested headings, e.g. in a list item, belong to the section above.
            if token.type != "heading_open" or token.level != 0 or not token.map:
                continue
            title = " ".join(tokens[index + 1].content.split())
            found.append((int(token.tag[1:]), title, token.map[0]))
        headings = []
        for position, (level, title, start) in enumerate(found):
            # A section ends at the next heading of the same or a higher level.
            following = (
                found[index][2]
                for index in range(position + 1, len(found))
                if found[index][0] <= level
            )
            headings.append(
                Heading(level, title, start, next(following, len(self._lines)))
            )
        return tuple(headings)

    def section(self, heading: str) -> str:
        """Return a section, including its subsections, selected by its heading.

        Boundary newlines are trimmed; other content whitespace is preserved.

        Args:
            heading: The heading as a ``#`` line, e.g. ``"## Step"``. The level
                must match; whitespace in the title is collapsed. Underlined
                headings are selected the same way.

        Raises:
            hiraku.errors.ValidationError: If ``heading`` is not a ``#`` line or
                several sections have that heading.
            hiraku.errors.SectionNotFoundError: If no section has that heading.

        Examples:
            >>> Markdown("# Intro\\n\\n## Step\\nDo it.").section("## Step")
            '## Step\\nDo it.'
            >>> markdown = Markdown("# Intro\\n\\n## Step")
            >>> markdown.section("## Missing")  # doctest: +ELLIPSIS
            Traceback (most recent call last):
            ...
            hiraku.errors.SectionNotFoundError: Section '## Missing' not found; ...
        """
        match = _SELECTOR.match(heading.strip()) if isinstance(heading, str) else None
        if match is None:
            raise ValidationError(
                f"Select a section by its heading, e.g. '## Step'; got {heading!r}"
            )
        level, title = len(match.group(1)), " ".join(match.group(2).split())
        found = [
            item
            for item in self.headings
            if item.level == level and item.title == title
        ]
        if len(found) > 1:
            raise ValidationError(f"Several sections have the heading {heading!r}")
        if not found:
            available = ", ".join(f"'{item}'" for item in self.headings) or "none"
            raise SectionNotFoundError(
                f"Section {heading!r} not found; headings: {available}"
            )
        return "\n".join(self._lines[found[0].start : found[0].end]).strip("\n")


_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def placeholders(markdown: str) -> set[str]:
    """Return the names of the ``{{name}}`` placeholders in Markdown text.

    Args:
        markdown: Text to scan for placeholders.

    Examples:
        >>> sorted(placeholders("Hello {{name}}, score {{ score }}"))
        ['name', 'score']
    """
    return set(_PLACEHOLDER.findall(markdown))


def render(markdown: str, values: Mapping[str, Any]) -> str:
    """Replace each ``{{name}}`` placeholder with its value.

    Booleans render as ``true`` and ``false``, ``None`` as an empty string,
    and other values with ``str``.

    Raises:
        hiraku.errors.ValidationError: If a placeholder has no value.

    Args:
        markdown: Text containing placeholders to replace.
        values: Values keyed by placeholder name.

    Examples:
        >>> render("Hello {{name}}. Score: {{score}}.", {"name": "Ada", "score": 4})
        'Hello Ada. Score: 4.'
        >>> render("{{enabled}} {{missing}}", {"enabled": True})
        Traceback (most recent call last):
        ...
        hiraku.errors.ValidationError: No value for placeholders: missing
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
