"""Reusable marimo widgets and rendering for Hiraku dashboards.

Components consume prepared tables or BaseRecord objects without loading or
saving project files or querying tracking sources. The notebook owns selection state;
the optional Markdown editor owns drafts and delegates saves to a callback.
Branding stays in the Studio view.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import altair as alt
import marimo as mo
import pandas as pd

from ..base import BaseRecord
from ..errors import HirakuError
from ..io import dump_yaml
from ..record import Record
from .data import plot_frame, run_text, safe_url, tradeoff_frame, tree_rows

if TYPE_CHECKING:
    from ..workflows.graph import WorkflowGraph


def workflow_graph(graph: WorkflowGraph | None) -> mo.Html:
    """Render a workflow's declared graph without executing its handlers."""
    if graph is None:
        return mo.md("No workflow selected.")
    # SVG text labels avoid oversized HTML label bounds in Studio.
    config = '%%{init: {"htmlLabels": false}}%%\n'
    return mo.mermaid(config + graph.to_mermaid())


def record_selector(records: Sequence[BaseRecord], *, label: str) -> mo.ui.dropdown:
    """Select a record ID independently of other dashboard selections."""
    identifiers = [record.id for record in records]
    return mo.ui.dropdown(
        identifiers,
        value=next(iter(identifiers), None),
        label=label,
        searchable=True,
        full_width=True,
        disabled=not identifiers,
    )


def markdown_view(
    record: BaseRecord | None,
    *,
    editor: MarkdownEditor | None = None,
    empty_message: str = "This record has no Markdown.",
) -> mo.Html:
    """Render a BaseRecord's front matter and Markdown body.

    Accept any subtype, including contexts and research records. Loading,
    parsing, and persistence belong to the caller. Pass an editor to opt into
    editing; without one this is a read-only viewer.
    """
    if editor is not None:
        return editor.render(record, empty_message=empty_message)
    if record is None:
        return mo.md("No record selected.")
    body = mo.md(record.markdown if record.markdown.strip() else empty_message)
    metadata = mo.Html(f"<pre>{html.escape(dump_yaml(record.to_dict()))}</pre>")
    return mo.vstack([mo.accordion({record.id: metadata}), body])


@dataclass
class _MarkdownDraft:
    record: BaseRecord
    markdown: str
    error: str = ""


class MarkdownEditor:
    """Session-local Markdown drafts with explicit Save and Cancel controls.

    Create once in a notebook cell, then pass to ``markdown_view`` in another.
    The callback persists a draft and returns the saved record. Only persisted
    records are editable; front matter remains read-only.
    """

    def __init__(
        self,
        on_save: Callable[[BaseRecord, str], BaseRecord],
        on_change: Callable[[], None] = lambda: None,
    ) -> None:
        self._on_save = on_save
        self._on_change = on_change
        self._drafts: dict[Path, _MarkdownDraft] = {}
        # Marimo's widget registry holds weak references.
        self._widgets: list[Any] = []

    def _refresh(self) -> None:
        self._on_change()

    def _start(self, record: BaseRecord) -> None:
        assert record.path is not None
        self._drafts[record.path] = _MarkdownDraft(
            record.model_copy(deep=True), str(record.markdown)
        )
        self._refresh()

    def _cancel(self, path: Path) -> None:
        self._drafts.pop(path, None)
        self._refresh()

    def _save(self, record: BaseRecord) -> None:
        draft = self._drafts[record.path]
        try:
            saved = self._on_save(draft.record, draft.markdown)
        except (HirakuError, OSError, ValueError) as exc:
            draft.error = str(exc)
            self._refresh()
            return
        record.markdown = saved.markdown
        self._cancel(record.path)

    def render(
        self,
        record: BaseRecord | None,
        *,
        empty_message: str = "This record has no Markdown.",
    ) -> mo.Html:
        """Show the record or its draft, retaining drafts across selections."""
        if record is None:
            self._widgets = []
            return markdown_view(None)
        draft = self._drafts.get(record.path)
        if draft is None:
            edit = mo.ui.button(
                label="✎",
                tooltip="Edit Markdown",
                disabled=record.path is None,
                on_click=lambda _: self._start(record),
            )
            self._widgets = [edit]
            controls = mo.hstack([edit], justify="end")
            return mo.vstack(
                [
                    mo.Html(f'<div class="hiraku-markdown-controls">{controls}</div>'),
                    markdown_view(record, empty_message=empty_message),
                ]
            )
        editor = mo.ui.code_editor(
            value=draft.markdown,
            language="markdown",
            min_height=250,
            on_change=lambda value: setattr(draft, "markdown", value),
        )
        cancel = mo.ui.button(
            label="Cancel", on_click=lambda _: self._cancel(record.path)
        )
        save = mo.ui.button(
            label="Save", kind="success", on_click=lambda _: self._save(record)
        )
        self._widgets = [editor, cancel, save]
        controls = mo.hstack([cancel, save], justify="end")
        metadata = mo.Html(
            f"<pre>{html.escape(dump_yaml(draft.record.to_dict()))}</pre>"
        )
        message = (
            mo.callout(mo.plain_text(draft.error), kind="danger")
            if draft.error
            else mo.md("Draft — changes are saved only when you click Save.")
        )
        return mo.vstack(
            [
                mo.Html(f'<div class="hiraku-markdown-controls">{controls}</div>'),
                mo.accordion({record.id: metadata}),
                editor,
                message,
            ]
        )


def record_tree(
    records: Sequence[Record],
    selected_id: str | None,
    on_select: Callable[[str], Any],
) -> tuple[mo.Html, dict[str, mo.ui.button]]:
    """Render optional record navigation and return its live button references.

    Keep the buttons in a notebook variable. Selection is supplied by the
    caller so this tree can share state with other components.
    """
    children: dict[str | None, list[Record]] = {}
    for record in records:
        children.setdefault(record.parent_id, []).append(record)
    buttons: dict[str, mo.ui.button] = {}
    fragments = ["<ul>"]
    stack = [(record, False) for record in reversed(children.get(None, []))]
    while stack:
        record, closing = stack.pop()
        if closing:
            fragments.append("</ul></li>")
            continue
        badges = " ".join(
            f'<span class="badge">{html.escape(value)}</span>'
            for value in (record.type, record.status)
            if value
        )
        button = mo.ui.button(
            label=(
                '<span class="hiraku-record-node">'
                '<span class="dot" aria-hidden="true"></span>'
                f'<span class="record-id">{html.escape(record.id)}</span> {badges}'
                "</span>"
            ),
            full_width=True,
            on_click=lambda _, identifier=record.id: on_select(identifier),
        )
        buttons[record.id] = button
        selected = ' aria-current="true"' if record.id == selected_id else ""
        fragments.append(f'<li><div class="record-row"{selected}>{button.text}</div>')
        nested = children.get(record.id, [])
        if nested:
            fragments.append("<ul>")
            stack.append((record, True))
            stack.extend((child, False) for child in reversed(nested))
        else:
            fragments.append("</li>")
    fragments.append("</ul>")
    content = "".join(fragments) if buttons else "No records yet."
    return (
        mo.Html(f'<nav class="record-tree" aria-label="Records">{content}</nav>'),
        buttons,
    )


def run_table(
    records_df: pd.DataFrame,
    runs_df: pd.DataFrame,
    metric: str | None,
    initial_record: str | None = None,
    *,
    selected_key: str | None = None,
    on_select: Callable[[str | None], Any] | None = None,
) -> mo.ui.table:
    """Build the selectable record/run table, retaining its hidden row keys."""
    _rows = tree_rows(records_df, runs_df, metric)

    def _style(row, column):
        style = {"fontWeight": "600"} if row["key"].startswith("record:") else {}
        if column == "Record / run":
            style["paddingLeft"] = f"{0.5 + 1.25 * row['depth']}rem"
        return style

    _initial = [
        index
        for index, row in enumerate(_rows)
        if row["key"] == (selected_key or f"record:{initial_record}")
    ]
    tree = mo.ui.table(
        _rows,
        selection="single",
        initial_selection=_initial,
        on_change=(lambda rows: on_select(rows[0]["key"] if rows else None))
        if on_select
        else None,
        hidden_columns=["key", "depth"] if _rows else None,
        page_size=50,
        show_column_summaries=False,
        show_data_types=False,
        show_download=False,
        format_mapping={
            metric: lambda value: f"{value:.4g}" if isinstance(value, float) else value
        }
        if metric
        else None,
        style_cell=lambda row, column, value: _style(_rows[int(row)], column),
    )
    return tree


def run_details(selected_run: Mapping[str, Any] | None) -> mo.Html:
    """Render run provenance and a safe tracking link, or a selection prompt."""
    if selected_run is None:
        _content = mo.md("Select a run in the tree to see its details.")
    else:
        # The link and the source name come from the run's tracking backend.
        _url = safe_url(selected_run["url"])
        _source = selected_run["source"] or "tracking backend"
        _content = mo.vstack(
            [
                mo.md(f"[Open in {_source} ↗]({_url})") if _url else mo.md(""),
                mo.Html(f"<pre>{html.escape(run_text(selected_run))}</pre>"),
            ]
        )
    return mo.as_html(_content)


def performance_controls(
    metrics: Sequence[str], default_metric: str | None
) -> mo.ui.dropdown:
    """Create the metric selector for the performance plot."""
    return mo.ui.dropdown(metrics, value=default_metric, label="Metric (y axis)")


def tradeoff_controls(
    records_df: pd.DataFrame, metrics: Sequence[str]
) -> tuple[mo.ui.dropdown, mo.ui.dropdown, mo.ui.dropdown, mo.ui.dropdown]:
    """Create independent metric and objective selectors for the trade-off plot."""
    x_default = metrics[0] if metrics else None
    y_default = metrics[1] if len(metrics) > 1 else x_default
    x_metric = mo.ui.dropdown(metrics, value=x_default, label="X metric")
    y_metric = mo.ui.dropdown(metrics, value=y_default, label="Y metric")

    def direction(metric_name):
        return next(
            (
                item["direction"]
                for declared in records_df["metrics"]
                if isinstance(declared, list)
                for item in declared
                if item["name"] == metric_name
            ),
            "minimize",
        )

    x_objective = mo.ui.dropdown(
        ["minimize", "maximize"],
        value=direction(x_default) if x_default else "minimize",
        label="X objective",
    )
    y_objective = mo.ui.dropdown(
        ["minimize", "maximize"],
        value=direction(y_default) if y_default else "minimize",
        label="Y objective",
    )
    return x_metric, x_objective, y_metric, y_objective


def performance_plot(
    runs_df: pd.DataFrame,
    *,
    scope: str | None,
    metric: str | None,
    selected_run: Mapping[str, Any] | None = None,
) -> mo.Html:
    """Plot one metric over execution order for an already scoped run table."""
    _points, _missing = plot_frame(runs_df, metric)
    _notes = [f"{len(runs_df)} runs under **{scope}**, numbered by execution order."]
    if _missing:
        _notes.append(f"{_missing} without a numeric `{metric}` are not shown.")
    if _points.empty:
        _chart = mo.md("No runs to plot.")
    else:
        _base = alt.Chart(_points).encode(
            x=alt.X(
                "number:Q",
                title="Run number (execution order)",
                axis=alt.Axis(format="d", tickMinStep=1),
                scale=alt.Scale(nice=False, padding=16),
            ),
            y=alt.Y("value:Q", title=metric, scale=alt.Scale(zero=False)),
        )
        _line = _base.mark_line(strokeWidth=2, opacity=0.3)
        _dots = _base.mark_point(filled=True, size=90, opacity=1).encode(
            color=alt.Color("record:N", title="Record"),
            tooltip=[
                alt.Tooltip("number:Q", title="Run #"),
                alt.Tooltip("run:N", title="Run"),
                alt.Tooltip("record:N", title="Record"),
                alt.Tooltip("started:N", title="Started"),
                alt.Tooltip("value:Q", title=metric, format=".4g"),
            ],
        )
        _layers = [_line, _dots]
        if selected_run is not None:
            _layers.append(
                _base.transform_filter(alt.datum.run == selected_run["run"]).mark_point(
                    size=320, strokeWidth=3, color="#f59e0b", filled=False
                )
            )
        _chart = (
            alt.layer(*_layers)
            .properties(width="container", height=340)
            .configure(background="transparent")
            .configure_view(stroke=None)
        )
    _content = mo.vstack([mo.md(" ".join(_notes)), _chart])
    return mo.as_html(_content)


def tradeoff_plot(
    runs_df: pd.DataFrame,
    *,
    x_metric: str | None,
    y_metric: str | None,
    x_objective: str = "minimize",
    y_objective: str = "minimize",
) -> mo.Html:
    """Compare two metrics and highlight non-dominated runs in the supplied table."""
    if not x_metric or not y_metric:
        _comparison = mo.md("No numeric metrics are available to compare.")
    elif x_metric == y_metric:
        _comparison = mo.callout("Choose two different metrics.", kind="info")
    else:
        _points, _missing = tradeoff_frame(
            runs_df,
            x_metric,
            y_metric,
            x_objective,
            y_objective,
        )
        if _points.empty:
            _comparison = mo.callout(
                f"No runs have numeric values for both metrics ({_missing} excluded).",
                kind="info",
            )
        else:
            _highlighted = _points.loc[_points["non_dominated"]]
            _note = (
                f"**{len(_points)} comparable runs** · "
                f"**{len(_highlighted)} strong trade-offs**"
            )
            if _missing:
                _note += f" · {_missing} runs excluded for missing or non-finite values"
            _base = alt.Chart(_points).encode(
                x=alt.X(
                    "x:Q",
                    title=f"{x_metric} ({x_objective})",
                    scale=alt.Scale(zero=False),
                ),
                y=alt.Y(
                    "y:Q",
                    title=f"{y_metric} ({y_objective})",
                    scale=alt.Scale(zero=False),
                ),
                color=alt.Color("record:N", title="Record"),
                tooltip=[
                    alt.Tooltip("run:N", title="Run"),
                    alt.Tooltip("record:N", title="Record"),
                    alt.Tooltip("x:Q", title=x_metric, format=".4g"),
                    alt.Tooltip("y:Q", title=y_metric, format=".4g"),
                    alt.Tooltip("non_dominated:N", title="Strong trade-off"),
                ],
            )
            _chart = (
                alt.layer(
                    _base.mark_circle(size=100, opacity=0.55),
                    _base.transform_filter(alt.datum.non_dominated).mark_point(
                        shape="diamond", filled=True, size=220, strokeWidth=1
                    ),
                )
                .properties(width="container", height=420)
                .interactive()
                .configure(background="transparent")
                .configure_view(stroke=None)
            )
            _table = _highlighted.drop(columns="non_dominated").rename(
                columns={
                    "run": "Run",
                    "record": "Record",
                    "x": x_metric,
                    "y": y_metric,
                }
            )
            _comparison = mo.vstack(
                [
                    mo.md(_note),
                    mo.md(
                        "A highlighted run has a strong trade-off: no comparable "
                        "run matches or improves both metrics while improving "
                        "at least one."
                    ),
                    _chart,
                    mo.ui.table(_table, selection=None, show_download=False),
                ]
            )
    _content = _comparison
    return mo.as_html(_content)
