from pathlib import Path

import pytest

from hiraku.project import Project
from hiraku.record import Record


def _save_markdown(project: Project):
    def save(record: Record, markdown: str) -> Record:
        record.markdown = markdown
        project.vault.save(record)
        return record

    return save


def test_markdown_view_opts_into_editing_explicitly(tmp_path: Path) -> None:
    pytest.importorskip("marimo")
    pytest.importorskip("altair")
    from hiraku.dashboard.components import MarkdownEditor, markdown_view

    config = Project.create(tmp_path, install_skill=False)
    record = Record.create("notes", type="task", project=config)
    editor = MarkdownEditor(_save_markdown(config))

    plain = markdown_view(record, empty_message="No notes yet.").text
    editable = markdown_view(record, editor=editor, empty_message="No notes yet.").text
    assert "hiraku-markdown-controls" not in plain
    assert "hiraku-markdown-controls" in editable
    assert "No notes yet." in plain
    assert "No notes yet." in editable

    editor._widgets[0]._update(1)
    assert "marimo-code-editor" in markdown_view(record, editor=editor).text
    assert "marimo-code-editor" not in markdown_view(record).text
    assert "No record selected." in markdown_view(None, editor=editor).text


def test_editor_retains_drafts_across_selection_and_cancels(tmp_path: Path) -> None:
    pytest.importorskip("marimo")
    pytest.importorskip("altair")
    from hiraku.dashboard.components import MarkdownEditor

    config = Project.create(tmp_path, install_skill=False)
    first = Record.create("first", type="task", markdown="Original", project=config)
    other = Record.create("other", type="task", project=config)
    editor = MarkdownEditor(_save_markdown(config))
    editor.render(first)
    editor._widgets[0]._update(1)
    assert type(editor._drafts[first.path].markdown) is str
    editor.render(first)
    editor._widgets[0]._update("Draft")

    assert "marimo-code-editor" not in editor.render(other).text
    assert "Draft" in editor.render(first).text
    editor._widgets[1]._update(1)

    assert "marimo-code-editor" not in editor.render(first).text
    assert config.vault.get(first.id).markdown == "Original"


def test_editor_saves_and_retains_failed_draft(tmp_path: Path) -> None:
    pytest.importorskip("marimo")
    pytest.importorskip("altair")
    from hiraku.dashboard.components import MarkdownEditor

    config = Project.create(tmp_path, install_skill=False)
    record = Record.create("notes", type="task", markdown="Original", project=config)
    editor = MarkdownEditor(_save_markdown(config))
    editor._start(record)
    editor.render(record)
    editor._widgets[0]._update("Saved body")
    editor._widgets[2]._update(1)
    assert record.markdown == "Saved body"
    assert config.vault.get(record.id).markdown == "Saved body"
    assert "marimo-code-editor" not in editor.render(record).text

    editor._start(record)
    editor._drafts[record.path].markdown = "Retained draft"
    record.path.unlink()
    editor._save(record)
    rendered = editor.render(record).text
    assert "Retained draft" in rendered
    assert editor._drafts[record.path].error
    assert "persisted file" in rendered
    assert record.markdown == "Saved body"
