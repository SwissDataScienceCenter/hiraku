"""Reading and writing `## Heading` sections inside a `Node`'s free-form markdown body.

`hiraku.records.Record` gives every record exactly one field for prose:
`markdown`, a flat string (see `Record`'s docstring — there is no structured
per-section API the way the old, pre-`hiraku` `hiraku_auto` had with
`set_section`/`append_section` on its own bespoke record type). The design,
discover, triage and analyze workflows all want to write a few conventional
headed sections — Findings, Conclusions, Decisions and next steps, and so on
— without clobbering whatever is already there, so this module provides that
on top of the plain string `hiraku` gives us, rather than asking `hiraku` to
change its own model.

A "section" here is a line starting with `## ` up to (but not including) the
next such line, or the end of the string.
"""

from __future__ import annotations

import re


def _pattern(heading: str) -> re.Pattern[str]:
    escaped = re.escape(heading.strip())
    return re.compile(
        rf"^## {escaped}[ \t]*\r?\n(.*?)(?=^## |\Z)",
        re.MULTILINE | re.DOTALL,
    )


def read_section(markdown: str, heading: str) -> str:
    """The body of `## {heading}`, stripped, or `""` if the section is absent."""
    match = _pattern(heading).search(markdown or "")
    return match.group(1).strip() if match else ""


def set_section(markdown: str, heading: str, body: str) -> str:
    """Replace `## {heading}`'s body, or append the section if it doesn't exist yet.

    Existing sections keep their position and their relative order; a new
    section is appended at the end. Blank/whitespace-only `body` still writes
    the heading — an explicit empty section is different from no section at
    all (it says the workflow looked and found nothing, not that it never ran).
    """
    block = f"## {heading}\n\n{body.strip()}\n\n"
    pattern = _pattern(heading)
    base = markdown or ""
    if pattern.search(base):
        return pattern.sub(block, base, count=1)
    prefix = base if base.endswith("\n") or not base.strip() else base + "\n"
    separator = "\n" if prefix.strip() else ""
    return f"{prefix}{separator}{block}"


def append_section(markdown: str, heading: str, body: str) -> str:
    """Add `body` to whatever `## {heading}` already holds, separated by a blank
    line."""
    existing = read_section(markdown, heading)
    combined = f"{existing}\n\n{body.strip()}" if existing else body
    return set_section(markdown, heading, combined)
