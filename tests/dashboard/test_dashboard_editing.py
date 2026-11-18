from pathlib import Path

import pytest

from hiraku.project import Project
from hiraku.record import Record, Vault


def _save_markdown(project: Project):
    def save(record: Record, markdown: str) -> Record:
        record.markdown = markdown
        project.vault.save(record, overwrite=True)
        return record

    return save


def test_markdown_view_opts_into_editing_explicitly(tmp_path: Path) -> None:
    pytest.importorskip("marimo")
    pytest.importorskip("altair")
    from hiraku.dashboard.components import MarkdownEditor, markdown_view

    config = Project.create(tmp_path, install_skill=False)
    record = config.vault.create("notes/notes", type="task", title="notes")
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
    first = config.vault.create(
        "first/first", type="task", markdown="Original", title="first"
    )
    other = config.vault.create("other/other", type="task", title="other")
    editor = MarkdownEditor(_save_markdown(config))
    editor.render(first)
    editor._widgets[0]._update(1)
    assert type(editor._drafts[first.id].markdown) is str
    editor.render(first)
    editor._widgets[0]._update("Draft")

    assert "marimo-code-editor" not in editor.render(other).text
    assert "Draft" in editor.render(first).text
    editor._widgets[1]._update(1)

    assert "marimo-code-editor" not in editor.render(first).text
    assert config.vault.load(first.id).markdown == "Original"


def test_editor_saves_and_retains_failed_draft(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("marimo")
    pytest.importorskip("altair")
    from hiraku.dashboard.components import MarkdownEditor

    config = Project.create(tmp_path, install_skill=False)
    record = config.vault.create(
        "notes/notes", type="task", markdown="Original", title="notes"
    )
    editor = MarkdownEditor(_save_markdown(config))
    editor._start(record)
    editor.render(record)
    editor._widgets[0]._update("Saved body")
    editor._widgets[2]._update(1)
    assert record.markdown == "Saved body"
    assert config.vault.load(record.id).markdown == "Saved body"
    assert "marimo-code-editor" not in editor.render(record).text

    editor._start(record)
    editor._drafts[record.id].markdown = "Retained draft"

    def fail(*args, **kwargs):
        raise OSError("write failed")

    monkeypatch.setattr(Vault, "save", fail)
    editor._save(record)
    rendered = editor.render(record).text
    assert "Retained draft" in rendered
    assert editor._drafts[record.id].error
    assert "write failed" in rendered
    assert record.markdown == "Saved body"
