"""Markdown section parsing and prompt lookup across workflows."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..errors import ValidationError, WorkflowError

if TYPE_CHECKING:
    from .core import Workflow

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


class PromptLibrary:
    """The ``workflow.md`` records of all workflows in a project, loaded together.

    Args:
        documents: Mapping from workflow ID to its workflow record.
    """

    def __init__(self, documents: Mapping[str, Workflow]) -> None:
        self._documents = dict(documents)

    def document(self, workflow_id: str) -> Workflow:
        """Return one workflow's document.

        Raises:
            hiraku.errors.WorkflowError: If the workflow has no loaded document.
        """
        try:
            return self._documents[workflow_id]
        except KeyError as exc:
            raise WorkflowError(
                f"Workflow {workflow_id!r} has no loaded workflow.md"
            ) from exc

    def resolve(self, workflow_id: str, reference: str | None = None) -> str:
        """Resolve a prompt reference relative to a workflow.

        Args:
            workflow_id: Workflow that local references resolve against.
            reference: ``None`` for the whole local document, ``"#section"`` for
                a local section, ``"other"`` for another workflow's document, or
                ``"other#section"`` for a section of another workflow.

        Returns:
            The referenced Markdown text.

        Raises:
            hiraku.errors.WorkflowError: If the reference is malformed or missing.
        """
        if reference is None or reference == "":
            return self.document(workflow_id).text.strip()
        target, separator, anchor = reference.partition("#")
        target = target or workflow_id
        if separator and not anchor:
            raise WorkflowError(f"Prompt reference {reference!r} has an empty anchor")
        if "/" in target or "\\" in target or target in {".", ".."}:
            raise WorkflowError(
                f"Prompt reference {reference!r} must name a project workflow"
            )
        document = self.document(target)
        return document.section(anchor) if anchor else document.text.strip()
