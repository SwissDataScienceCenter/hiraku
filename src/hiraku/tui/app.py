"""Textual application for browsing Hiraku research records."""

from __future__ import annotations

import json
from collections import defaultdict

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Footer,
    Header,
    Markdown,
    TabbedContent,
    TabPane,
    Tree,
)
from textual.widgets.tree import TreeNode

from ..config import HirakuConfig
from ..record import Record, list_records
from ..serialization import _json_value


class RecordTUI(App[None]):
    """Browse a project's record tree and inspect each record's contents."""

    TITLE = "Hiraku Records"
    SUB_TITLE = "Tab to a content pane · ↑/↓ scroll · read-only"
    CSS_PATH = "app.tcss"
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, config: HirakuConfig | None = None) -> None:
        super().__init__()
        self.hiraku_config = config
        self.records: dict[str, Record] = {}
        self.children_by_id: dict[str, list[Record]] = defaultdict(list)

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="browser-layout"):
            yield Tree("Research records", id="record-tree", data=None)
            with Vertical(id="record-panel"):
                yield Markdown(
                    "Select a record from the tree to inspect it.",
                    id="record-summary",
                )
                with TabbedContent(id="record-tabs"):
                    with (
                        TabPane("Overview", id="overview-tab"),
                        VerticalScroll(id="overview-scroll"),
                    ):
                        yield Markdown(
                            "Choose a record to see its research context.",
                            id="record-overview",
                        )
                    with (
                        TabPane("Fields", id="fields-tab"),
                        VerticalScroll(id="fields-scroll"),
                    ):
                        yield Markdown(
                            "Effective values and their defining records.",
                            id="record-fields",
                        )
                    with (
                        TabPane("Markdown", id="markdown-tab"),
                        VerticalScroll(id="markdown-scroll"),
                    ):
                        yield Markdown(
                            "The selected record's Markdown body will appear here.",
                            id="record-content",
                        )
        yield Footer()

    def on_mount(self) -> None:
        config = self.hiraku_config or HirakuConfig.discover()
        self.hiraku_config = config
        self.records = {record.id: record for record in list_records(config=config)}
        tree = self.query_one("#record-tree", Tree)
        for record in self.records.values():
            self.children_by_id[record.parent_id or ""].append(record)
        for siblings in self.children_by_id.values():
            siblings.sort(key=lambda record: record.id)

        tree.root.set_label(f"Research records  ·  {len(self.records)}")
        tree.root.expand()
        for record in self.children_by_id[""]:
            self._add_record_node(tree.root, record, depth=0)
        if not self.records:
            self.query_one("#record-summary", Markdown).update(
                "## No research records yet"
            )
            self.query_one("#record-overview", Markdown).update(
                "This project does not contain any records."
            )

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
            label.append("  ★ active", style="bold green")
        children = self.children_by_id[record.id]
        node = parent.add(
            label,
            data=record.id,
            expand=depth == 0 and bool(children),
        )
        for child in children:
            self._add_record_node(node, child, depth=depth + 1)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[str | None]) -> None:
        if event.node.data is not None:
            self._show_record(event.node.data)

    def on_tree_node_selected(self, event: Tree.NodeSelected[str | None]) -> None:
        if event.node.data is not None:
            self._show_record(event.node.data)

    def _show_record(self, record_id: str) -> None:
        record = self.records[record_id]
        resolved = record.resolve(config=self.hiraku_config)
        active = bool(
            self.hiraku_config and self.hiraku_config.active_record_id == record.id
        )
        parent = f"Parent: `{record.parent_id}`" if record.parent_id else "Root record"
        child_count = len(self.children_by_id[record.id])
        relation = "child" if child_count == 1 else "children"
        badges = [value for value in (record.type, record.status) if value]
        if active:
            badges.append("active")
        summary = " · ".join(f"**{value}**" for value in badges) or "Research record"
        self.query_one("#record-summary", Markdown).update(
            f"# {record.id}\n\n{summary}  \n{parent}  ·  {child_count} {relation}"
        )

        overview = [
            f"## {record.id}",
            "",
            f"{parent}.",
            f"This record has **{child_count} {relation}**.",
        ]
        narrative_fields = {
            "goal": "Goal",
            "question": "Research question",
            "summary": "Summary",
            "hypothesis": "Hypothesis",
            "expectation": "Expectation",
            "conclusion": "Conclusion",
            "verdict": "Verdict",
        }
        for field, title in narrative_fields.items():
            value = resolved.values.get(field)
            if value is None or value == "":
                continue
            rendered = (
                value
                if isinstance(value, str)
                else json.dumps(_json_value(value), ensure_ascii=False, indent=2)
            )
            overview.extend(["", f"### {title}", "", rendered])
        if record.commit:
            overview.append(f"\nCommit: `{record.commit}`")
        if record.related_to:
            related = ", ".join(f"`{item}`" for item in record.related_to)
            overview.append(f"\nRelated records: {related}")
        self.query_one("#record-overview", Markdown).update("\n\n".join(overview))

        stored = record.model_dump(mode="json", exclude={"path", "markdown"})
        effective = _json_value(resolved.values)
        sources = _json_value(resolved.sources)
        fields = [
            "## Stored fields",
            "",
            "```json",
            json.dumps(stored, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
            "## Effective values",
            "",
            "Inherited values are resolved through the record's parents.",
            "",
            "```json",
            json.dumps(effective, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
            "## Field sources",
            "",
            "| Field | Defined by |",
            "| --- | --- |",
        ]
        fields.extend(
            f"| `{key}` | `{value}` |" for key, value in sorted(sources.items())
        )
        self.query_one("#record-fields", Markdown).update("\n".join(fields))

        content = record.markdown.strip() or "_This record has no Markdown body._"
        self.query_one("#record-content", Markdown).update(content)


def main() -> None:
    """Run the record browser in the current Hiraku project."""
    RecordTUI().run()


if __name__ == "__main__":
    main()
