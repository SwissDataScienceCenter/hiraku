"""Validate that the unreleased changelog contains release notes."""

from __future__ import annotations

import re
from pathlib import Path

UNRELEASED_HEADING = re.compile(r"^## \[Unreleased\]\s*$")
H2_HEADING = re.compile(r"^##\s+")
CHANGELOG_PATH = Path("CHANGELOG.md")


def has_unreleased_entries(path: Path) -> bool:
    """Return whether the Unreleased section contains at least one bullet."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SystemExit(f"Could not read changelog {path}: {exc}") from exc

    try:
        start = next(
            index for index, line in enumerate(lines) if UNRELEASED_HEADING.match(line)
        )
    except StopIteration as exc:
        raise SystemExit(f"Could not find an [Unreleased] section in {path}") from exc

    section = []
    for line in lines[start + 1 :]:
        if H2_HEADING.match(line):
            break
        section.append(line)

    return any(line.lstrip().startswith("- ") for line in section)


def main() -> None:
    if not has_unreleased_entries(CHANGELOG_PATH):
        raise SystemExit(
            f"Add at least one bullet under [Unreleased] in {CHANGELOG_PATH}."
        )

    print(f"Changelog check passed: {CHANGELOG_PATH}")


if __name__ == "__main__":
    main()
