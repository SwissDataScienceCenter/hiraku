from __future__ import annotations

import pandas as pd
import pytest

pytest.importorskip("marimo")
pytest.importorskip("altair")

from hiraku.base import BaseRecord
from hiraku.context import Context
from hiraku.dashboard import components as hc
from hiraku.dashboard.data import (
    RECORD_COLUMNS,
    RUN_COLUMNS,
    arrow_frame,
)
from hiraku.record import Record


def test_markdown_view_renders_generic_content_and_preserves_placeholders() -> None:
    content = hc.markdown_view(
        BaseRecord(
            id="workflow",
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
        BaseRecord(id="context", markdown=body), empty_message="No notes yet."
    ).text

    assert "No notes yet." in content
    assert "Front matter" not in content
    assert "id: context" in content
    assert content.index("id: context") < content.index("No notes yet.")


def test_markdown_front_matter_is_displayed_as_text() -> None:
    content = hc.markdown_view(
        BaseRecord(id="notes", markdown="Notes", title="<script>bad()</script>")
    ).text

    assert "id: notes" in content
    assert "bad()" in content
    assert "<script>bad()</script>" not in content


@pytest.mark.parametrize("record_type", [BaseRecord, Context, Record])
def test_markdown_view_accepts_any_record(record_type) -> None:
    record = record_type(
        id="notes",
        markdown="## Findings\n\n**Useful** results.",
        custom="metadata",
    )
    original = record.model_dump()

    content = hc.markdown_view(record).text

    assert "<h3>notes</h3>" not in content
    assert "Findings" in content
    assert "<strong>Useful</strong>" in content
    assert "Front matter" not in content
    assert "id: notes" in content
    assert "custom: metadata" in content
    assert content.index("id: notes") < content.index("Findings")
    assert record.model_dump() == original


def test_markdown_view_handles_missing_or_empty_records() -> None:
    assert "No record selected." in hc.markdown_view(None).text
    empty = hc.markdown_view(BaseRecord(id="empty")).text
    assert "This record has no Markdown." in empty
    assert "id: empty" in empty


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


def test_record_tree_hierarchy_selection_and_callbacks() -> None:
    records = [
        Record(
            id="child",
            parent_id="root",
            type="hypothesis",
            status="open",
            decision="deferred",
        ),
        Record(id="root", type="task"),
    ]
    selections = []
    tree, buttons = hc.record_tree(records, "child", selections.append)

    assert tree.text.count("<ul>") == 2
    assert tree.text.count('aria-current="true"') == 1
    assert "hypothesis" in tree.text
    assert "open" in tree.text
    assert "deferred" not in tree.text
    assert "<style>" not in tree.text
    assert "justify-content" not in tree.text
    assert list(buttons) == ["root", "child"]
    buttons["child"]._update(1)
    buttons["root"]._update(1)
    assert selections == ["child", "root"]


def test_record_tree_handles_empty_and_deep_hierarchies() -> None:
    empty, buttons = hc.record_tree([], None, lambda _: None)
    assert "No records yet." in empty.text
    assert buttons == {}
    records = [
        Record(id=f"r{index}", parent_id=f"r{index - 1}" if index else None)
        for index in range(1050)
    ]
    tree, buttons = hc.record_tree(records, None, lambda _: None)
    assert len(buttons) == 1050
    assert tree.text.count("<li>") == tree.text.count("</li>") == 1050


def test_record_tree_escapes_badges() -> None:
    tree, _buttons = hc.record_tree(
        [Record(id="notes", status="<script>bad()</script>")], None, lambda _: None
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
    assert (f'href="{url}"' in details) is linked
    assert "javascript:alert" not in details


def test_independent_plot_controls_use_declared_defaults() -> None:
    records = pd.DataFrame(
        {
            "metrics": [
                [
                    {"name": "loss", "direction": "minimize"},
                    {"name": "accuracy", "direction": "maximize"},
                ]
            ]
        }
    )

    metric = hc.performance_controls(["loss", "accuracy"], "loss")
    controls = hc.tradeoff_controls(records, ["loss", "accuracy"])

    assert metric.value == "loss"
    assert [control.value for control in controls] == [
        "loss",
        "minimize",
        "accuracy",
        "maximize",
    ]


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
    content = hc.performance_plot(runs, scope="root", metric="loss").text
    assert "No runs to plot." in content


@pytest.mark.parametrize("plot", [hc.performance_plot, hc.tradeoff_plot])
def test_independent_plots_render_runs_and_report_excluded_values(plot) -> None:
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
        content = plot(
            runs, scope="root", metric="loss", selected_run={"run": "best"}
        ).text
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
        assert "3 runs under" in content
        assert "1 without a numeric" in content
        assert "f59e0b" in content
    else:
        assert "2 comparable runs" in content
        assert "1 strong trade-offs" in content
        assert "1 runs excluded" in content


FILE = {
    "key": "0",
    "path": "results/out.csv",
    "produced_by": "workflow · make",
    "record": "workflow",
    "used_by": ["report · summarize"],
    "last_run": "run-1",
    "command": "hiraku launch make --record workflow -- --output results/out.csv",
}


def test_file_table_shows_producing_and_using_steps() -> None:
    table = hc.file_table([FILE])

    assert "results/out.csv" in table.text
    assert "workflow · make" in table.text
    assert "report · summarize" in table.text
    # The row key identifies a selection but is not a user-facing column.
    assert ">key<" not in table.text


def test_file_table_accepts_an_empty_project() -> None:
    table = hc.file_table([])

    assert table.value.empty


def test_file_details_show_the_command_and_escape_content() -> None:
    entries = [{**FILE, "path": "<script>bad()</script>"}]

    details, _buttons = hc.file_details(entries, [{"key": "0"}], lambda _: None)
    content = details.text

    assert "&lt;script&gt;bad()&lt;/script&gt;" in content
    assert "<script>bad()</script>" not in content
    assert "hiraku launch make --record workflow" in content


def test_file_details_explain_how_to_select_a_file() -> None:
    details, buttons = hc.file_details([], None, lambda _: None)

    assert "Select a file" in details.text
    assert buttons == {}


def test_file_details_select_the_producing_record_and_run() -> None:
    selections: list[str] = []
    details, buttons = hc.file_details([FILE], [{"key": "0"}], selections.append)

    assert list(buttons) == ["record", "run"]
    assert "Record workflow" in details.text
    assert "Run run-1" in details.text
    buttons["run"]._update(1)
    buttons["record"]._update(1)
    assert selections == ["run:run-1", "record:workflow"]

    # A file built by a rule that does not launch a record has no record button.
    _details, buttons = hc.file_details(
        [{**FILE, "record": None, "last_run": None}], [{"key": "0"}], lambda _: None
    )
    assert buttons == {}


def test_artifact_tables_separate_sop_references_and_hide_empty_file_fields() -> None:
    from hiraku.dashboard.data import ProjectRuns, artifact_rows
    from hiraku.registration import Registration

    record = Record(
        id="dataset-abc",
        type="dataset",
        registration=Registration.model_validate({
            "namespace": "cohort",
            "key": ["S1", "CyTOF"],
            "metadata": {"sample_id": "S1", "technology": "CyTOF"},
            "coverage": "exhaustive",
            "artifacts": [{"key": "a", "display_path": "data/a.fcs",
                           "availability": "present"}],
            "documentation": [{"key": "sop-1", "filename": "gated.fcs",
                               "source": "cytof/gating.md",
                               "description": "Export gated events.",
                               "expected_availability": "missing"}],
        }).to_record_value(),
    )
    rows = artifact_rows(ProjectRuns(records=[record], runs=[]))

    observed = hc.artifact_table(rows).data
    references = hc.artifact_table(rows, documentation=True).data
    [reference] = references.to_dict("records") if hasattr(
        references, "to_dict") else references

    assert len(observed) == 1
    assert reference["Dataset"] == "S1 · CyTOF"
    assert reference["Referenced filename"] == "gated.fcs"
    assert reference["Description"] == "Export gated events."
    assert reference["SOP"] == "cytof/gating.md"
    selected = [row for row in rows if row["key"].startswith("documentation:")]
    details, _ = hc.artifact_details(rows, selected, on_select=lambda key: None)
    assert "null" not in details.text
    assert "gated.fcs" in details.text
