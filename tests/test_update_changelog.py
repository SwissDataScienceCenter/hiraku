from datetime import date
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

script_path = Path(__file__).parents[1] / "scripts" / "update_changelog.py"
script_spec = spec_from_file_location("update_changelog", script_path)
assert script_spec is not None and script_spec.loader is not None
update_changelog_module = module_from_spec(script_spec)
script_spec.loader.exec_module(update_changelog_module)
update_changelog = update_changelog_module.update_changelog


def test_update_changelog_adds_dated_version_heading(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text("## [Unreleased]\n\n- New feature\n", encoding="utf-8")

    update_changelog(path, "0.1.2", date(2026, 11, 24))

    assert path.read_text(encoding="utf-8") == (
        "## [Unreleased]\n\n## [0.1.2] - 2026-11-24\n\n- New feature\n"
    )


@pytest.mark.parametrize(
    "content",
    ["## [0.1.1] - 2026-09-30\n", "## [Unreleased]\n\n## [Unreleased]\n"],
)
def test_update_changelog_requires_exactly_one_unreleased_heading(tmp_path, content):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="exactly one"):
        update_changelog(path, "0.1.2", date(2026, 11, 24))


def test_update_changelog_rejects_duplicate_version_heading(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(
        "## [Unreleased]\n\n- Release note\n\n## [0.1.2] - 2026-11-24\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="already contains"):
        update_changelog(path, "0.1.2", date(2026, 11, 24))


def test_update_changelog_requires_unreleased_bullet(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text("## [Unreleased]\n\n## [0.1.1] - 2026-09-30\n", encoding="utf-8")

    with pytest.raises(ValueError, match="at least one bullet"):
        update_changelog(path, "0.1.2", date(2026, 11, 24))
