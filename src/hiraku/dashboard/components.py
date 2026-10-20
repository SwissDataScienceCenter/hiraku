"""Reusable marimo widgets and rendering for Hiraku dashboards.

Components consume prepared tables or BaseRecord objects without loading or
saving files or querying tracking sources. The notebook owns reactive state
and reads widget values before passing them to the renderers. Branding stays
in the Studio view.
"""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from typing import Any

import altair as alt
import marimo as mo
import pandas as pd

from ..base import BaseRecord
from ..io import dump_yaml
from .data import plot_frame, run_text, safe_url, tradeoff_frame, tree_rows


def markdown_view(
    record: BaseRecord | None,
    *,
    empty_message: str = "This record has no Markdown.",
) -> mo.Html:
    """Render a BaseRecord's front matter and Markdown body.

    Accept any subtype, including contexts and research records. Loading,
    parsing, and persistence belong to the caller.
    """
    if record is None:
        return mo.md("No record selected.")
    body = mo.md(record.markdown if record.markdown.strip() else empty_message)
    metadata = mo.Html(f"<pre>{html.escape(dump_yaml(record.to_dict()))}</pre>")
    return mo.vstack([mo.accordion({record.id: metadata}), body])


def run_table(
    records_df: pd.DataFrame,
    runs_df: pd.DataFrame,
    metric: str | None,
    initial_record: str | None = None,
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
        if row["key"] == f"record:{initial_record}"
    ]
    tree = mo.ui.table(
        _rows,
        selection="single",
        initial_selection=_initial,
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


def metric_controls(
    records_df: pd.DataFrame,
    metrics: Sequence[str],
    default_metric: str | None,
) -> tuple[
    mo.ui.dropdown,
    mo.ui.dropdown,
    mo.ui.dropdown,
    mo.ui.dropdown,
    mo.ui.dropdown,
    mo.ui.dropdown,
]:
    """Create the mode, metric, and trade-off selectors for metric analysis.

    Return the widgets so the notebook can retain them and read their values
    in a downstream cell; rendering a chart does not create new controls.
    """
    plot_mode = mo.ui.dropdown(
        ["Performance over runs", "Metric trade-offs"],
        value="Performance over runs",
        label="View",
    )
    metric = mo.ui.dropdown(metrics, value=default_metric, label="Metric (y axis)")
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
    return plot_mode, metric, x_metric, x_objective, y_metric, y_objective


def metric_analysis(
    runs_df: pd.DataFrame,
    *,
    scope: str | None,
    mode: str,
    metric: str | None,
    x_metric: str | None,
    y_metric: str | None,
    x_objective: str,
    y_objective: str,
    selected_run: Mapping[str, Any] | None = None,
) -> mo.Html:
    """Render the current analysis mode for an already scoped table of runs."""
    if mode == "Performance over runs":
        _points, _missing = plot_frame(runs_df, metric)
        _notes = [
            f"{len(runs_df)} runs under **{scope}**, numbered by execution order."
        ]
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
                    _base.transform_filter(
                        alt.datum.run == selected_run["run"]
                    ).mark_point(size=320, strokeWidth=3, color="#f59e0b", filled=False)
                )
            _chart = (
                alt.layer(*_layers)
                .properties(width="container", height=340)
                .configure(background="transparent")
                .configure_view(stroke=None)
            )
        _content = mo.vstack([mo.md(" ".join(_notes)), _chart])
    else:
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
                    "No runs have numeric values for both metrics "
                    f"({_missing} excluded).",
                    kind="info",
                )
            else:
                _highlighted = _points.loc[_points["non_dominated"]]
                _note = (
                    f"**{len(_points)} comparable runs** · "
                    f"**{len(_highlighted)} strong trade-offs**"
                )
                if _missing:
                    _note += (
                        f" · {_missing} runs excluded for missing or non-finite values"
                    )
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
