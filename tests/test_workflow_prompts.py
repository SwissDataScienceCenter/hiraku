from __future__ import annotations

from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.errors import RecordNotFoundError, ValidationError, WorkflowError
from hiraku.workflow import Workflow, discover_workflows, load_workflow
from hiraku.workflow.prompts import normalize_anchor
from hiraku.workflow.service import WorkflowService

DOCUMENT = """\
# Research

Introduction text.

### Not a section boundary

Still orientation.

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


def research() -> Workflow:
    return Workflow(id="research", markdown=DOCUMENT)


def test_normalize_anchor() -> None:
    assert normalize_anchor("Approve the Launch!") == "approve-the-launch"
    assert normalize_anchor("  Step  1:  Select ") == "step-1-select"
    assert normalize_anchor("keep-hyphens") == "keep-hyphens"


def test_orientation_ends_at_first_second_level_heading() -> None:
    document = Workflow(id="research", markdown=DOCUMENT)
    assert document.orientation.startswith("# Research")
    assert "Still orientation." in document.orientation
    assert "Pick one." not in document.orientation
    assert Workflow(id="x", markdown="# Only\n\nText\n").orientation == (
        "# Only\n\nText"
    )


def test_section_includes_subsections_and_ignores_fenced_headings() -> None:
    workflow = research()
    select = workflow.section("select")
    assert select.startswith("## Select")
    assert "Nested detail." in select
    assert "## Not a heading" in select
    assert "Ask first." not in select
    details = workflow.section("details")
    assert details.startswith("### Details\n\nNested detail.")
    assert details.endswith("```")
    assert workflow.section("approve-the-launch").endswith("Ask first.")
    assert "not-a-heading" not in Workflow(id="r", markdown=DOCUMENT).anchors


def test_whole_document_text() -> None:
    assert research().text == DOCUMENT.strip()


def test_missing_section() -> None:
    with pytest.raises(WorkflowError, match="#missing not found"):
        research().section("missing")


def test_anchors_that_normalize_to_the_same_name_are_rejected() -> None:
    with pytest.raises(ValidationError, match="share the anchor #select"):
        Workflow(id="dup", markdown="## Select\n\n## select!\n")
    document = Workflow(id="ok", markdown="## Select\n")
    with pytest.raises(ValidationError, match="share the anchor"):
        document.markdown = "## A\n\n## a\n"


def test_sections_follow_markdown_changes() -> None:
    document = Workflow(id="doc", markdown="## Old\n\nText\n")
    document.markdown = "# Intro\n\n## New\n\nText\n"
    assert document.anchors == ["intro", "new"]
    assert document.orientation == "# Intro"


def test_workflow_md_is_a_registry_record(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False, install_mcp=False)
    document = load_workflow("autoresearch-python", config=config)
    assert document.id == "autoresearch-python"
    assert document.path == config.workflows_dir / "autoresearch-python" / "workflow.md"
    assert document.text.startswith("# Autoresearch")
    assert "id: autoresearch-python" not in document.text
    assert discover_workflows(config) == [
        "autodataset",
        "autoresearch-python",
        "guided-research",
        "hiraku-agent",
    ]

    document.summary = "Bounded research"
    document.save()
    reloaded = load_workflow("autoresearch-python", config=config)
    assert reloaded.to_dict() == {
        "id": "autoresearch-python",
        "success_status": "finished",
        "summary": "Bounded research",
    }
    assert reloaded.section("record") == document.section("record")


@pytest.mark.parametrize(
    ("content", "error", "message"),
    [
        ("---\nid: other\n---\n# X\n", ValidationError, "must match the workflow"),
        ("# No front matter\n", ValidationError, "front matter"),
        (None, RecordNotFoundError, "not found"),
    ],
)
def test_invalid_workflow_documents_disable_the_workflow(
    tmp_path: Path, content: str | None, error: type[Exception], message: str
) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False, install_mcp=False)
    path = config.workflows_dir / "autodataset" / "workflow.md"
    if content is None:
        path.unlink()
    else:
        path.write_text(content)
    with pytest.raises(error, match=message):
        load_workflow("autodataset", config=config)
    service = WorkflowService(config)
    assert "autodataset" not in service.workflows
    assert "autoresearch-python" in service.workflows
    with pytest.raises(error, match=message):
        service.activate("autodataset")
