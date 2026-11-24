"""Add the current project version to the changelog."""

from __future__ import annotations

import re
import tomllib
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNRELEASED_HEADING = re.compile(r"(?m)^## \[Unreleased\][ \t]*$")


def update_changelog(path: Path, version: str, release_date: date) -> None:
    """Insert a dated version heading below the Unreleased heading."""
    content = path.read_text(encoding="utf-8")
    headings = list(UNRELEASED_HEADING.finditer(content))
    if len(headings) != 1:
        raise ValueError("Expected exactly one ## [Unreleased] heading")

    version_heading = re.compile(rf"(?m)^## \[{re.escape(version)}\](?:\s|$)")
    if version_heading.search(content):
        raise ValueError(f"Changelog already contains a heading for {version}")

    section_start = headings[0].end()
    next_section = re.search(r"(?m)^##[ \t]+", content[section_start:])
    section_end = section_start + next_section.start() if next_section else len(content)
    unreleased = content[section_start:section_end]
    if not any(line.lstrip().startswith("- ") for line in unreleased.splitlines()):
        raise ValueError("Add at least one bullet under ## [Unreleased]")

    replacement = f"## [Unreleased]\n\n## [{version}] - {release_date.isoformat()}"
    content = UNRELEASED_HEADING.sub(lambda _: replacement, content, count=1)
    path.write_text(content, encoding="utf-8")


def main() -> None:
    """Read the project version and update CHANGELOG.md."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    update_changelog(ROOT / "CHANGELOG.md", version, date.today())


if __name__ == "__main__":
    main()
