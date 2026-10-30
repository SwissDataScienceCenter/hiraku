import pytest
from pydantic import ValidationError as PydanticValidationError

from hiraku.errors import SectionNotFoundError, ValidationError
from hiraku.markdown import Markdown, placeholders, render
from hiraku.record import Record

DOCUMENT = """\
# Research

Introduction text.

### Not a section boundary

Still introduction.

## Select

Pick one.

### Details

Nested detail.

```markdown
## Not a heading
```

## Approve the Launch!

Ask first.
"""


@pytest.fixture
def markdown() -> Markdown:
    return Markdown(DOCUMENT)


def test_headings_are_top_level_sections_with_their_lines(markdown: Markdown) -> None:
    assert [str(item) for item in markdown.headings] == [
        "# Research",
        "### Not a section boundary",
        "## Select",
        "### Details",
        "## Approve the Launch!",
    ]
    nested = Markdown("- item\n\n  ## In a list\n\n> ## Quoted\n")
    assert nested.headings == ()


def test_first_level_section_contains_the_whole_document(markdown: Markdown) -> None:
    document = markdown.section("# Research")
    assert document.startswith("# Research\n\nIntroduction text.")
    assert document.endswith("Ask first.")


def test_section_includes_subsections_and_ignores_fenced_headings(
    markdown: Markdown,
) -> None:
    select = markdown.section("## Select")
    assert select.startswith("## Select")
    assert "Nested detail." in select
    assert "## Not a heading" in select
    assert "Ask first." not in select
    details = markdown.section("### Details")
    assert details.startswith("### Details\n\nNested detail.")
    assert details.endswith("```")


@pytest.mark.parametrize(
    "selector", ["## Approve the Launch!", "  ##  Approve  the Launch! ##"]
)
def test_section_selector_normalizes_heading_whitespace(
    markdown: Markdown, selector: str
) -> None:
    assert markdown.section(selector).endswith("Ask first.")


@pytest.mark.parametrize("selector", ["select", "Select", "#Select", "####### X", "##"])
def test_section_selector_must_be_a_heading_line(
    markdown: Markdown, selector: str
) -> None:
    with pytest.raises(ValidationError, match="Select a section by its heading"):
        markdown.section(selector)


def test_underlined_headings_are_selected_as_heading_lines() -> None:
    markdown = Markdown("Title\n=====\n\nIntro.\n\nStep\n----\n\nDo it.\n")
    assert markdown.section("## Step") == "Step\n----\n\nDo it."
    assert markdown.section("# Title").startswith("Title\n=====\n\nIntro.")


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"], ids=["lf", "crlf", "cr"])
def test_section_lines_follow_markdown_line_endings(newline: str) -> None:
    text = "# A\n\n## B\nb \u2028 still b\n## C\n"
    markdown = Markdown(text.replace("\n", newline))
    assert markdown.section("## B") == "## B\nb \u2028 still b"


def test_repeated_headings_make_section_selection_ambiguous() -> None:
    markdown = Markdown("## Notes\n\nA\n\n## Notes\n\nB\n")
    with pytest.raises(ValidationError, match="Several sections"):
        markdown.section("## Notes")


@pytest.mark.parametrize(
    ("selector", "message"),
    [
        ("## Details", "headings: '# Research'"),
        ("## Not a heading", "'## Not a heading' not found"),
        ("## Missing", "'## Missing' not found"),
    ],
)
def test_missing_section(markdown: Markdown, selector: str, message: str) -> None:
    with pytest.raises(SectionNotFoundError, match=message):
        markdown.section(selector)


def test_markdown_field_stays_a_string() -> None:
    record = Record(id="doc", markdown="## Old\n")
    record.markdown = "## New\n"
    assert isinstance(record.markdown, Markdown)
    assert record.markdown == "## New\n"
    assert type(record.model_dump()["markdown"]) is str


@pytest.mark.parametrize("value", [None, 42])
def test_markdown_field_rejects_invalid_values(value) -> None:
    with pytest.raises(PydanticValidationError, match="Input should be a valid string"):
        Record(id="doc", markdown=value)
    record = Record(id="doc", markdown="## Original\n")
    with pytest.raises(PydanticValidationError, match="Input should be a valid string"):
        record.markdown = value
    assert record.markdown == "## Original\n"


@pytest.mark.parametrize("ending", ["", "\n\n## Next\nOther."])
def test_section_preserves_content_whitespace(ending: str) -> None:
    section = "  ## Code\n\n    payload  \t"
    markdown = Markdown(section + "\n\n" + ending)
    assert markdown.section("## Code") == section


def test_many_peer_headings_have_adjacent_boundaries() -> None:
    markdown = Markdown("".join(f"## Step {index}\n" for index in range(4000)))
    assert len(markdown.headings) == 4000
    assert [(item.start, item.end) for item in markdown.headings] == [
        (index, index + 1) for index in range(3999)
    ] + [(3999, 4001)]


def test_placeholders_discovers_unique_valid_names() -> None:
    text = "{{name}} {{ name }} {{\t_other2\t}} {{2bad}} {{bad-name}}"
    assert placeholders(text) == {"name", "_other2"}
    assert placeholders("No placeholders.") == set()


def test_render_replaces_repeated_and_padded_placeholders() -> None:
    assert render("{{name}} / {{ name }}", {"name": "Ada", "unused": 1}) == "Ada / Ada"
    assert render("No placeholders.", {}) == "No placeholders."


@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, "true"), (False, "false"), (None, ""), ("text", "text"), (42, "42")],
)
def test_render_value_conversions(value, expected: str) -> None:
    assert render("{{value}}", {"value": value}) == expected


def test_render_reports_all_missing_values() -> None:
    with pytest.raises(ValidationError, match="No value for placeholders: a, z$"):
        render("{{z}} {{a}} {{ z }} {{provided}}", {"provided": "yes"})
