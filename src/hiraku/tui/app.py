"""Textual application for browsing and editing Hiraku research records."""

from __future__ import annotations

import json
from collections import defaultdict

from pydantic import ValidationError as PydanticValidationError
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Footer,
    Header,
    Markdown,
    MarkdownViewer,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
    Tree,
)
from textual.widgets.tree import TreeNode

from ..config import HirakuConfig
from ..errors import HirakuError
from ..record import Record, list_records
from ..serialization import _json_value


class RecordTUI(App[None]):
    """Browse the record hierarchy and inspect or edit record Markdown."""

    TITLE = "Hiraku"
    SUB_TITLE = "Research records"
    CSS_PATH = "app.tcss"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("ctrl+b", "toggle_sidebar", "Sidebar"),
        ("ctrl+left", "narrow_sidebar", "Narrow sidebar"),
        ("ctrl+right", "widen_sidebar", "Widen sidebar"),
        ("ctrl+r", "refresh_records", "Refresh"),
        ("e", "edit_markdown", "Edit Markdown"),
        ("ctrl+s", "save_markdown", "Save"),
        ("escape", "cancel_edit", "Cancel edit"),
    ]

    def __init__(self, config: HirakuConfig | None = None) -> None:
        super().__init__()
        self.hiraku_config = config
        self.records: dict[str, Record] = {}
        self.children_by_id: dict[str, list[Record]] = defaultdict(list)
        self.selected_id: str | None = None
        self.editing_id: str | None = None
        self.original_markdown: str | None = None
        self.sidebar_width: int | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="workspace"):
            with Vertical(id="record-sidebar"):
                yield Static("RECORDS", id="sidebar-title")
                yield Tree("Research records", id="record-tree", data=None)
            with Vertical(id="detail-panel"):
                with Horizontal(id="record-heading"):
                    yield Markdown(
                        "Select a record from the tree.", id="record-summary"
                    )
                    yield Static("", id="record-state")
                with TabbedContent(id="record-tabs"):
                    with (
                        TabPane("Overview", id="overview-pane"),
                        VerticalScroll(id="overview-scroll"),
                    ):
                        yield Markdown(
                            "Research context for the selected record appears here.",
                            id="record-overview",
                        )
                    with (
                        TabPane("Markdown", id="markdown-pane"),
                        Vertical(id="markdown-panel"),
                    ):
                        with Horizontal(id="markdown-controls"):
                            yield Button("Edit", id="edit-button", disabled=True)
                            yield Button("Save", id="save-button", disabled=True)
                            yield Button("Cancel", id="cancel-button", disabled=True)
                            yield Static("", id="edit-status")
                        yield MarkdownViewer(
                            "Select a record to inspect its Markdown.",
                            show_table_of_contents=False,
                            open_links=False,
                            id="markdown-preview",
                        )
                        yield TextArea(
                            id="markdown-editor",
                            language="markdown",
                            show_line_numbers=True,
                            read_only=True,
                        )
                    with (
                        TabPane("Fields", id="fields-pane"),
                        VerticalScroll(id="fields-scroll"),
                    ):
                        yield Markdown(
                            "Stored and inherited fields appear here.",
                            id="record-fields",
                        )
        yield Footer()

    def on_mount(self) -> None:
        self.hiraku_config = self.hiraku_config or HirakuConfig.discover()
        self._load_records()

    def _load_records(self) -> None:
        assert self.hiraku_config is not None
        previous_id = self.selected_id
        self.records = {
            record.id: record for record in list_records(config=self.hiraku_config)
        }
        self.children_by_id = defaultdict(list)
        for record in self.records.values():
            self.children_by_id[record.parent_id or ""].append(record)
        for siblings in self.children_by_id.values():
            siblings.sort(key=lambda record: record.id)

        tree = self.query_one("#record-tree", Tree)
        tree.root.remove_children()
        tree.root.set_label(f"Research records · {len(self.records)}")
        tree.root.expand()
        for record in self.children_by_id[""]:
            self._add_record_node(tree.root, record, depth=0)

        if not self.records:
            tree.root.add_leaf("No research records yet")
            self.selected_id = None
            self._clear_record()
        elif previous_id in self.records:
            self._show_record(previous_id)
        else:
            self.selected_id = None
            self._clear_record()

    def _add_record_node(
        self,
        parent: TreeNode[str | None],
        record: Record,
        *,
        depth: int,
    ) -> None:
        label = Text(record.id, style="bold")
        if record.type:
            label.append(f"  {record.type}", style="cyan")
        if record.status:
            label.append(f"  {record.status}", style="yellow")
        if self.hiraku_config and self.hiraku_config.active_record_id == record.id:
            label.append("  ★", style="bold green")
        children = self.children_by_id[record.id]
        node = parent.add(
            label,
            data=record.id,
            expand=depth == 0 and bool(children),
        )
        for child in children:
            self._add_record_node(node, child, depth=depth + 1)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[str | None]) -> None:
        if event.node.data in self.records:
            self._select_record(event.node.data)

    def on_tree_node_selected(self, event: Tree.NodeSelected[str | None]) -> None:
        if event.node.data in self.records:
            self._select_record(event.node.data)

    def _select_record(self, record_id: str) -> None:
        if self.editing_id is not None and record_id != self.editing_id:
            self.query_one("#edit-status", Static).update(
                "Save or cancel the Markdown edit before changing records."
            )
            return
        self.selected_id = record_id
        self._show_record(record_id)

    def _show_record(self, record_id: str) -> None:
        record = self.records[record_id]
        assert self.hiraku_config is not None
        resolved = record.resolve(config=self.hiraku_config)
        active = self.hiraku_config.active_record_id == record.id
        parent = f"Parent: `{record.parent_id}`" if record.parent_id else "Root record"
        child_count = len(self.children_by_id[record.id])
        child_label = "child" if child_count == 1 else "children"
        state = [value for value in (record.type, record.status) if value]
        if active:
            state.append("active")
        self.query_one("#record-summary", Markdown).update(
            f"## {record.id}\n**{' · '.join(state) or 'Record'}**  "
            f"\n{parent} · {child_count} {child_label}"
        )
        self.query_one("#record-state", Static).update(
            f"{record.commit[:10]}" if record.commit else ""
        )
        self.query_one("#record-overview", Markdown).update(
            self._overview(record, resolved.values, parent, child_count, child_label)
        )
        self.query_one("#record-fields", Markdown).update(
            self._fields(record, resolved.values, resolved.sources)
        )

        body = record.markdown.strip() or "_This record has no Markdown body._"
        preview = self.query_one("#markdown-preview", MarkdownViewer)
        preview.document.update(body)
        preview.show_table_of_contents = (
            sum(line.lstrip().startswith("#") for line in record.markdown.splitlines())
            >= 4
        )
        self.query_one("#markdown-editor", TextArea).load_text(record.markdown)
        self.query_one("#edit-status", Static).update("")
        self.query_one("#edit-button", Button).disabled = self.editing_id is not None

    @staticmethod
    def _overview(
        record: Record,
        values: dict[str, object],
        parent: str,
        child_count: int,
        child_label: str,
    ) -> str:
        lines = [
            f"## {record.id}",
            "",
            f"{parent}. This record has **{child_count} {child_label}**.",
        ]
        for field, title in (
            ("goal", "Goal"),
            ("question", "Research question"),
            ("summary", "Summary"),
            ("hypothesis", "Hypothesis"),
            ("expectation", "Expectation"),
            ("conclusion", "Conclusion"),
            ("decision", "Decision"),
        ):
            value = values.get(field)
            if value is None or value == "":
                continue
            rendered = (
                value
                if isinstance(value, str)
                else json.dumps(_json_value(value), ensure_ascii=False, indent=2)
            )
            lines.extend(["", f"### {title}", "", rendered])
        if record.commit:
            lines.extend(["", f"Commit: `{record.commit}`"])
        if record.related_to:
            related = ", ".join(f"`{item}`" for item in record.related_to)
            lines.extend(["", f"Related records: {related}"])
        return "\n".join(lines)

    @staticmethod
    def _fields(
        record: Record, values: dict[str, object], sources: dict[str, object]
    ) -> str:
        stored = record.model_dump(mode="json", exclude={"path", "markdown"})
        sections = [
            "## Stored fields",
            "",
            "```json",
            json.dumps(
                _json_value(stored), ensure_ascii=False, indent=2, sort_keys=True
            ),
            "```",
            "",
            "## Effective fields",
            "",
            "```json",
            json.dumps(
                _json_value(values), ensure_ascii=False, indent=2, sort_keys=True
            ),
            "```",
            "",
            "## Field sources",
            "",
            "| Field | Defined by |",
            "| --- | --- |",
        ]
        sections.extend(
            f"| `{key}` | `{source}` |" for key, source in sorted(sources.items())
        )
        return "\n".join(sections)

    def _clear_record(self) -> None:
        self.query_one("#record-summary", Markdown).update(
            "## No research records yet"
            if not self.records
            else "Select a record from the tree."
        )
        self.query_one("#record-state", Static).update("")
        self.query_one("#record-overview", Markdown).update(
            "This project does not contain any records."
            if not self.records
            else "Choose a record to inspect its research context."
        )
        self.query_one("#record-fields", Markdown).update("")
        self.query_one("#markdown-preview", MarkdownViewer).document.update("")
        self.query_one("#markdown-editor", TextArea).load_text("")
        self.query_one("#edit-button", Button).disabled = True

    def action_edit_markdown(self) -> None:
        if self.selected_id is None or self.editing_id is not None:
            return
        self.editing_id = self.selected_id
        self.original_markdown = self.records[self.selected_id].markdown
        self.query_one("#markdown-preview", MarkdownViewer).display = False
        editor = self.query_one("#markdown-editor", TextArea)
        editor.read_only = False
        editor.display = True
        self.query_one("#edit-button", Button).disabled = True
        self.query_one("#save-button", Button).disabled = False
        self.query_one("#cancel-button", Button).disabled = False
        self.query_one("#edit-status", Static).update("Editing Markdown")
        self.query_one("#record-tree", Tree).disabled = True
        editor.focus()

    def action_save_markdown(self) -> None:
        if self.editing_id is None:
            return
        record = self.records[self.editing_id]
        record.markdown = self.query_one("#markdown-editor", TextArea).text
        try:
            record.save()
        except (HirakuError, PydanticValidationError, ValueError, OSError) as exc:
            self.query_one("#edit-status", Static).update(f"Save failed: {exc}")
            return
        record_id = self.editing_id
        self.original_markdown = None
        self._finish_edit()
        self._show_record(record_id)
        self.query_one("#edit-status", Static).update("Saved")

    def action_cancel_edit(self) -> None:
        if self.editing_id is None:
            return
        record_id = self.editing_id
        original = self.original_markdown or ""
        self.records[record_id].markdown = original
        self.query_one("#markdown-editor", TextArea).load_text(original)
        self.original_markdown = None
        self._finish_edit()
        self._show_record(record_id)
        self.query_one("#edit-status", Static).update("Changes discarded")

    def _finish_edit(self) -> None:
        self.editing_id = None
        self.query_one("#markdown-preview", MarkdownViewer).display = True
        editor = self.query_one("#markdown-editor", TextArea)
        editor.read_only = True
        editor.display = False
        self.query_one("#edit-button", Button).disabled = self.selected_id is None
        self.query_one("#save-button", Button).disabled = True
        self.query_one("#cancel-button", Button).disabled = True
        self.query_one("#record-tree", Tree).disabled = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "edit-button":
            self.action_edit_markdown()
        elif event.button.id == "save-button":
            self.action_save_markdown()
        elif event.button.id == "cancel-button":
            self.action_cancel_edit()

    def action_toggle_sidebar(self) -> None:
        sidebar = self.query_one("#record-sidebar")
        sidebar.display = not sidebar.display

    def action_widen_sidebar(self) -> None:
        self._adjust_sidebar_width(4)

    def action_narrow_sidebar(self) -> None:
        self._adjust_sidebar_width(-4)

    def _adjust_sidebar_width(self, adjustment: int) -> None:
        sidebar = self.query_one("#record-sidebar")
        current = self.sidebar_width or sidebar.size.width or 42
        self.sidebar_width = min(60, max(28, current + adjustment))
        sidebar.styles.width = self.sidebar_width

    def action_refresh_records(self) -> None:
        if self.editing_id is not None:
            self.query_one("#edit-status", Static).update(
                "Save or cancel your Markdown edit before refreshing."
            )
            return
        try:
            self._load_records()
        except (HirakuError, PydanticValidationError, ValueError, OSError) as exc:
            self.query_one("#edit-status", Static).update(f"Refresh failed: {exc}")

    def action_quit(self) -> None:
        if self.editing_id is not None:
            self.query_one("#edit-status", Static).update(
                "Save or cancel the Markdown edit before quitting."
            )
            return
        self.exit()


def main() -> None:
    """Run the record browser in the current Hiraku project."""
    RecordTUI().run()


if __name__ == "__main__":
    main()
