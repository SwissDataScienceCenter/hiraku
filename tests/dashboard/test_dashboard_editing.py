from pathlib import Path

import pytest
from fmql import parse_file

from hiraku.config import HirakuConfig
from hiraku.record import Record, open_workspace


def test_markdown_view_opts_into_editing_explicitly(tmp_path: Path) -> None:
    pytest.importorskip("marimo")
    pytest.importorskip("altair")
    from hiraku.dashboard.components import MarkdownEditor, markdown_view

    config = HirakuConfig.init(
        tmp_path, install_skill=False, records_path="hiraku/records"
    )
    workspace = open_workspace(config)
    record = Record.create(workspace, "notes/notes.md", id="notes")
    config.set_active_record(record)
    editor = MarkdownEditor(lambda record, body: record.save_markdown(workspace, body))

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

    config = HirakuConfig.init(
        tmp_path, install_skill=False, records_path="hiraku/records"
    )
    workspace = open_workspace(config)
    first = Record.create(workspace, "first/first.md", id="first", markdown="Original")
    config.set_active_record(first)
    other = Record.create(workspace, "other/other.md", id="other")
    config.set_active_record(other)
    editor = MarkdownEditor(lambda record, body: record.save_markdown(workspace, body))
    editor.render(first)
    editor._widgets[0]._update(1)
    assert type(editor._drafts[first.path].markdown) is str
    editor.render(first)
    editor._widgets[0]._update("Draft")

    assert "marimo-code-editor" not in editor.render(other).text
    assert "Draft" in editor.render(first).text
    editor._widgets[1]._update(1)

    assert "marimo-code-editor" not in editor.render(first).text
    assert Record.from_packet(parse_file(first.path)).markdown == "Original"


def test_editor_saves_and_retains_failed_draft(tmp_path: Path) -> None:
    pytest.importorskip("marimo")
    pytest.importorskip("altair")
    from hiraku.dashboard.components import MarkdownEditor

    config = HirakuConfig.init(
        tmp_path, install_skill=False, records_path="hiraku/records"
    )
    workspace = open_workspace(config)
    record = Record.create(workspace, "notes/notes.md", id="notes", markdown="Original")
    config.set_active_record(record)
    editor = MarkdownEditor(lambda record, body: record.save_markdown(workspace, body))
    editor._start(record)
    editor.render(record)
    editor._widgets[0]._update("Saved body")
    editor._widgets[2]._update(1)
    assert record.markdown == "Saved body"
    assert Record.from_packet(parse_file(record.path)).markdown == "Saved body"
    assert "marimo-code-editor" not in editor.render(record).text

    editor._start(record)
    editor._drafts[record.path].markdown = "Retained draft"
    record.path.unlink()
    editor._save(record)
    rendered = editor.render(record).text
    assert "Retained draft" in rendered
    assert editor._drafts[record.path].error
    assert "notes.md" in rendered
    assert record.markdown == "Saved body"
