from __future__ import annotations

import pytest

pytest.importorskip("marimo")
pytest.importorskip("altair")

from hiraku.dashboard import components as hc
from hiraku.dashboard.data import RECORD_COLUMNS, RUN_COLUMNS, arrow_frame
from hiraku.record import Record


def test_markdown_view_renders_generic_content_and_preserves_placeholders() -> None:
    content = hc.markdown_view(
        Record(
            id="workflow",
            type="record",
            markdown="## Workflow\n\nRun **training**.\n\n"
            "- Keep {{record_id}}\n\n```python\nx = 1\n```",
        ),
    ).text

    assert "Workflow" in content
    assert "<strong>training</strong>" in content
    assert "<li>" in content
    assert "{{record_id}}" in content
    assert "<code" in content
    assert "Front matter" not in content


@pytest.mark.parametrize("body", ["", " \n\t"])
def test_markdown_view_handles_empty_bodies_without_hiding_metadata(body: str) -> None:
    content = hc.markdown_view(
        Record(id="context", type="context", markdown=body),
        empty_message="No notes yet.",
    ).text

    assert "No notes yet." in content
    assert "Front matter" not in content
    assert "type: context" in content
    assert content.index("type: context") < content.index("No notes yet.")


def test_markdown_front_matter_is_displayed_as_text() -> None:
    content = hc.markdown_view(
        Record(
            id="notes",
            type="record",
            markdown="Notes",
            title="<script>bad()</script>",
        )
    ).text

    assert "type: record" in content
    assert "bad()" in content
    assert "<script>bad()</script>" not in content


def test_markdown_view_accepts_record() -> None:
    record = Record(
        id="notes",
        type="record",
        markdown="## Findings\n\n**Useful** results.",
        custom="metadata",
    )
    original = record.model_dump()

    content = hc.markdown_view(record).text

    assert "<h3>notes</h3>" not in content
    assert "Findings" in content
    assert "<strong>Useful</strong>" in content
    assert "Front matter" not in content
    assert "type: record" in content
    assert "custom: metadata" in content
    assert content.index("type: record") < content.index("Findings")
    assert record.model_dump() == original


def test_markdown_view_handles_missing_or_empty_records() -> None:
    assert "No record selected." in hc.markdown_view(None).text
    empty = hc.markdown_view(Record(id="empty", type="record")).text
    assert "This record has no Markdown." in empty
    assert "type: record" in empty


def test_run_table_retains_initial_record_and_selection_keys() -> None:
    records = arrow_frame(
        [{"record": "root", "record_path": "root", "type": "task"}], RECORD_COLUMNS
    )
    runs = arrow_frame(
        [{"run": "r1", "record": "root", "metric.loss": 0.5}], RUN_COLUMNS
    )

    table = hc.run_table(records, runs, "loss", initial_record="root")

    assert table.value[0]["key"] == "record:root"
    assert "run:r1" in table.text
    assert "0.5" in table.text


def test_run_table_accepts_an_empty_project() -> None:
    table = hc.run_table(
        arrow_frame([], RECORD_COLUMNS), arrow_frame([], RUN_COLUMNS), None
    )

    assert table.value == []


@pytest.mark.parametrize("renderer", [hc.filesystem_tree, hc.record_tree])
def test_record_navigation_displays_title_and_secondary_id(renderer):
    tree, buttons = renderer(
        [Record(id="folder/record", title="Friendly title", type="note")],
        "folder/record",
        lambda _: None,
    )
    assert "Friendly title" in tree.text
    assert "folder/record" in tree.text
    assert "record-title" in tree.text
    assert "record-id" in tree.text
    assert set(buttons) == {"folder/record"}


def test_record_summary_links_and_collapsible_metadata():
    record = Record(
        id="example",
        title="Example",
        type="task",
        state="done",
        commit="a" * 40,
        branch="feature/test",
        hypothesis="<check>",
    )
    summary = hc.record_summary(
        record,
        repository_url="https://github.com/team/repo",
        runs=[
            {"run": "r1", "source": "mlflow", "url": "https://mlflow.test/r1"},
            {"run": "r2", "url": "javascript:alert(1)"},
        ],
        open_run_id="r2",
    ).text
    assert "https://github.com/team/repo/commit/" + "a" * 40 in summary
    assert "https://github.com/team/repo/tree/feature%2Ftest" in summary
    assert "https://mlflow.test/r1" in summary
    assert "javascript:" not in summary
    assert "&lt;check&gt;" in summary
    assert "Runs (2)" in summary
    assert '<details class="record-runs" open>' in summary
    assert '<details class="record-run" open><summary>r2</summary>' in summary
    collapsed_header = summary.split('<details class="record-run"><summary>r1', 1)[1]
    assert "https://mlflow.test/r1" in collapsed_header.split("</summary>", 1)[0]
    assert summary.count("https://mlflow.test/r1") == 1
    assert "<details" in summary and "Raw frontmatter" in summary
    plain = hc.record_summary(record).text
    assert "<a " not in plain


def test_record_filter_chips_keep_options_and_report_toggle_callbacks():
    records = [
        Record(id="a", type="note", state="draft"),
        Record(id="b", type="task", state="stable"),
    ]
    toggles = []
    filters, buttons = hc.record_filters(
        records, lambda field, value: toggles.append((field, value)), types=["note"]
    )
    assert set(buttons) == {
        ("type", "note"),
        ("type", "task"),
        ("state", "draft"),
        ("state", "stable"),
    }
    assert "record-filter-chip active" in filters.text
    buttons["type", "note"]._update(1)
    buttons["state", "stable"]._update(1)
    assert toggles == [("type", "note"), ("state", "stable")]
    empty, buttons = hc.record_filters([], lambda *_: None)
    assert not buttons
    assert "record-filter-group" not in empty.text


def test_filesystem_tree_ignores_lineage_and_preserves_path_selection():
    records = [
        Record(id="notes/child", type="note", parent_id="other"),
        Record(id="other", type="task", parent_id="notes/child"),
        Record(id="notes", type="task"),
    ]
    selections = []
    tree, buttons = hc.filesystem_tree(records, "notes/child", selections.append)
    assert set(buttons) == {"notes/child", "other", "notes"}
    assert 'record-folder-name">notes</span>' in tree.text
    assert "notes/child" in tree.text
    assert 'aria-current="true"' in tree.text
    buttons["notes/child"]._update(1)
    assert selections == ["notes/child"]


def test_filesystem_tree_filters_local_fields_and_retains_directories():
    records = [
        Record(id="group/a", type="note", state="draft"),
        Record(id="group/nested/b", type="task", state="stable"),
        Record(id="outside", type="note", state="stable"),
    ]
    tree, buttons = hc.filesystem_tree(
        records, "outside", lambda _: None, types=["task"], states=["stable"]
    )
    assert set(buttons) == {"group/nested/b"}
    assert 'record-folder-name">group</span>' in tree.text
    assert 'record-folder-name">nested</span>' in tree.text
    assert "aria-current" not in tree.text
    empty, buttons = hc.filesystem_tree(
        records, None, lambda _: None, types=["task"], states=["draft"]
    )
    assert not buttons
    assert "No matching records" in empty.text


def test_filesystem_tree_escapes_paths_and_handles_empty_records():
    tree, _ = hc.filesystem_tree(
        [Record(id="folder&/file&", type="<type>")], None, lambda _: None
    )
    assert "folder&amp;" in tree.text
    assert "file" in tree.text
    assert "<type>" not in tree.text
    empty, buttons = hc.filesystem_tree([], None, lambda _: None)
    assert "No matching records" in empty.text
    assert not buttons


def test_record_tree_hierarchy_selection_and_callbacks() -> None:
    records = [
        Record(
            id="child",
            parent_id="root",
            type="hypothesis",
            state="drafted",
            decision="deferred",
        ),
        Record(id="root", type="task"),
    ]
    selections = []
    tree, buttons = hc.record_tree(records, "child", selections.append)

    assert tree.text.count("<ul>") == 2
    assert tree.text.count('aria-current="true"') == 1
    assert "hypothesis" in tree.text
    assert "drafted" in tree.text
    assert "deferred" not in tree.text
    assert "<style>" not in tree.text
    assert "justify-content" not in tree.text
    assert list(buttons) == ["root", "child"]
    buttons["child"]._update(1)
    buttons["root"]._update(1)
    assert selections == ["child", "root"]


def test_lineage_filters_retain_ancestors_and_dim_context():
    from hiraku.dashboard.data import lineage_records

    records = [
        Record(id="root", type="task"),
        Record(id="elsewhere/child", parent_id="root", type="note", state="done"),
        Record(id="other", type="note", state="draft"),
        Record(id="cycle-a", parent_id="cycle-b", type="task"),
        Record(id="cycle-b", parent_id="cycle-a", type="note", state="done"),
        Record(id="orphan", parent_id="missing", type="note", state="done"),
    ]
    filtered = lineage_records(records, types=["note"], states=["done"])
    assert {record.id for record in filtered} == {
        "root",
        "elsewhere/child",
        "cycle-a",
        "cycle-b",
        "orphan",
    }
    tree, buttons = hc.record_tree(
        filtered,
        "elsewhere/child",
        lambda _: None,
        matching_ids={"elsewhere/child", "cycle-b", "orphan"},
    )
    assert set(buttons) == {record.id for record in filtered}
    assert tree.text.count("record-context") == 2
    assert tree.text.count('aria-current="true"') == 1


def test_record_tree_handles_empty_and_deep_hierarchies() -> None:
    empty, buttons = hc.record_tree([], None, lambda _: None)
    assert "No records yet." in empty.text
    assert buttons == {}
    records = [
        Record(
            id=f"r{index}",
            type="record",
            parent_id=f"r{index - 1}" if index else None,
        )
        for index in range(1050)
    ]
    tree, buttons = hc.record_tree(records, None, lambda _: None)
    assert len(buttons) == 1050
    assert tree.text.count("<li>") == tree.text.count("</li>") == 1050


def test_record_tree_includes_missing_parents_and_cycles() -> None:
    records = [
        Record(id=identifier, type="task", parent_id=parent)
        for identifier, parent in (
            ("child", "orphan"),
            ("orphan", "missing"),
            ("a", "b"),
            ("b", "a"),
            ("self", "self"),
        )
    ]
    selections = []

    tree, buttons = hc.record_tree(records, "b", selections.append)

    assert list(buttons) == ["orphan", "child", "a", "b", "self"]
    assert tree.text.count("<li>") == tree.text.count("</li>") == len(records)
    assert tree.text.count("<ul>") == tree.text.count("</ul>")
    assert tree.text.count('aria-current="true"') == 1
    buttons["b"]._update(1)
    assert selections == ["b"]


def test_record_tree_escapes_badges() -> None:
    tree, _buttons = hc.record_tree(
        [Record(id="notes", type="<script>bad()</script>")],
        None,
        lambda _: None,
    )
    assert "<script>bad()</script>" not in tree.text
    assert "bad()" in tree.text


@pytest.mark.parametrize(
    ("url", "linked"),
    [("https://mlflow.example/runs/r1", True), ("javascript:alert(1)", False)],
)
def test_run_details_escapes_content_and_only_links_safe_urls(
    url: str, linked: bool
) -> None:
    details = hc.run_details(
        {"run": "<script>bad()</script>", "source": "mlflow", "url": url}
    ).text

    assert "&lt;script&gt;bad()&lt;/script&gt;" in details
    assert "<script>bad()</script>" not in details
    assert ("Open in mlflow" in details) is linked
    assert "javascript:alert" not in details


@pytest.mark.parametrize(
    ("x_metric", "y_metric", "message"),
    [
        (None, None, "No numeric metrics are available"),
        ("loss", "loss", "Choose two different metrics."),
        ("loss", "accuracy", "No runs have numeric values"),
    ],
)
def test_tradeoff_plot_handles_empty_or_unusable_inputs(
    x_metric: str | None, y_metric: str | None, message: str
) -> None:
    runs = arrow_frame([], [*RUN_COLUMNS, "metric.loss", "metric.accuracy"])

    content = hc.tradeoff_plot(
        runs,
        x_metric=x_metric,
        y_metric=y_metric,
        x_objective="minimize",
        y_objective="maximize",
    )

    assert message in content.text


def test_performance_plot_handles_empty_runs() -> None:
    runs = arrow_frame([], [*RUN_COLUMNS, "metric.loss"])
    content = hc.performance_plot(runs, metric="loss").text
    assert "No runs to plot." in content


@pytest.mark.parametrize("plot", [hc.performance_plot, hc.tradeoff_plot])
def test_independent_plots_render_plottable_runs(plot) -> None:
    runs = arrow_frame(
        [
            {
                "run": "best",
                "record": "root",
                "metric.loss": 0.2,
                "metric.accuracy": 0.9,
            },
            {
                "run": "other",
                "record": "root",
                "metric.loss": 0.5,
                "metric.accuracy": 0.8,
            },
            {"run": "missing", "record": "root", "metric.loss": None},
        ],
        RUN_COLUMNS,
    )

    if plot is hc.performance_plot:
        content = plot(runs, metric="loss", selected_run={"run": "best"}).text
    else:
        content = plot(
            runs,
            x_metric="loss",
            y_metric="accuracy",
            x_objective="minimize",
            y_objective="maximize",
        ).text

    assert "best" in content
    assert "other" in content
    if plot is hc.performance_plot:
        assert "numbered by execution order" not in content
        assert "f59e0b" in content
    else:
        assert "diamond" not in content
        assert "non_dominated" not in content
        assert "comparable runs" not in content
