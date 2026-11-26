"""Reusable marimo widgets and rendering for Hiraku dashboards.

Components consume prepared tables or Record objects without loading or
saving project files or querying tracking sources. The notebook owns selection state.
Branding stays in the Studio view.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from urllib.parse import quote

import altair as alt
import marimo as mo
import pandas as pd

from ..io import dump_yaml
from ..record import Record
from .data import (
    RUN_TABLE_COLUMNS,
    lineage_records,
    plot_frame,
    run_table_metric_column,
    run_text,
    safe_url,
    tradeoff_frame,
    tree_rows,
)


def record_heading(record: Record) -> mo.Html:
    """Render the record title and the ID for Studio's copy action."""
    title = html.escape(record.title or record.id.rsplit("/", 1)[-1])
    identifier = html.escape(record.id)
    return mo.Html(
        '<div class="record-detail-header">'
        f'<h2 class="record-details-heading">{title}</h2>'
        '<div class="record-detail-id">'
        f'<button type="button" data-copy-record-id="{identifier}" '
        'class="record-copy-button" aria-label="Copy record ID" '
        f'title="Copy record ID"><code>{identifier}</code>'
        '<span aria-hidden="true"></span></button>'
        '<span class="record-copy-status" role="status"></span></div></div>'
    )


def markdown_view(
    record: Record | None,
    *,
    empty_message: str = "This record has no Markdown.",
    repository_url: str | None = None,
    runs: Sequence[Mapping[str, Any]] = (),
    open_run_id: str | None = None,
    show_id: bool = True,
) -> mo.Html:
    """Render a Record's front matter and Markdown body."""
    if record is None:
        return mo.md("No record selected.")
    body = mo.md(record.markdown if record.markdown.strip() else empty_message)
    metadata = record_summary(
        record,
        repository_url=repository_url,
        runs=runs,
        open_run_id=open_run_id,
        show_id=show_id,
    )
    return mo.vstack(
        [metadata, mo.Html(f'<div class="record-markdown">{body.text}</div>')]
    )


def record_summary(
    record: Record,
    *,
    repository_url: str | None = None,
    runs: Sequence[Mapping[str, Any]] = (),
    open_run_id: str | None = None,
    show_id: bool = True,
) -> mo.Html:
    """Render local metadata and expandable details for supplied runs."""
    rows = []
    values = {"id": record.id, **record.metadata}
    for field in (
        *(("id",) if show_id else ()),
        "state",
        "summary",
        "description",
        "goal",
        "hypothesis",
        "decision",
        "parent_id",
        "related_to",
        "commit",
        "branch",
    ):
        value = values.get(field)
        if value is None or value == "" or value == []:
            continue
        text = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
        display = html.escape(text)
        if field in {"commit", "branch"} and safe_url(repository_url):
            route = "commit" if field == "commit" else "tree"
            url = f"{repository_url.rstrip('/')}/{route}/{quote(text, safe='')}"
            display = (
                f'<a href="{html.escape(url, quote=True)}" target="_blank" '
                f'rel="noopener noreferrer">{display}</a>'
            )
        rows.append(f"<dt>{field}</dt><dd>{display}</dd>")
    items = []
    any_open = False
    for run in runs:
        label = html.escape(str(run["run"]))
        is_open = run["run"] == open_run_id
        any_open |= is_open
        expanded = " open" if is_open else ""
        items.append(
            f'<li><details class="record-run"{expanded}>'
            f"<summary>{label}{_run_link(run)}</summary>"
            f"{run_details(run, show_link=False).text}</details></li>"
        )
    expanded = " open" if any_open else ""
    raw = html.escape(dump_yaml(record.metadata))
    raw_label = "Raw frontmatter"
    if show_id:
        raw_label += f" · {html.escape(record.id)}"
    return mo.Html(
        f'<dl class="record-metadata">{"".join(rows)}</dl>'
        f'<details class="record-runs"{expanded}><summary>Runs ({len(items)})</summary>'
        f"<ul>{''.join(items)}</ul></details>"
        f'<details class="record-raw"><summary>{raw_label}</summary>'
        f"<pre>{raw}</pre></details>"
    )


def _record_label(record: Record) -> str:
    title = html.escape(record.title or record.id.rsplit("/", 1)[-1])
    return (
        '<span class="hiraku-record-node">'
        f'<span class="record-title" title="{title}">{title}</span>'
        f'<span class="record-type">{html.escape(record.type)}</span></span>'
    )


def record_tree(
    records: Sequence[Record],
    selected_id: str | None,
    on_select: Callable[[str], Any],
    *,
    matching_ids: set[str] | None = None,
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
    known = {record.id for record in records}
    roots = [record for record in records if record.parent_id not in known]
    # Remaining records seed components without roots, such as cycles.
    stack = [(record, False) for record in reversed(roots + list(records))]
    while stack:
        record, closing = stack.pop()
        if closing:
            fragments.append("</ul></li>")
            continue
        if record.id in buttons:
            continue
        button = mo.ui.button(
            label=_record_label(record),
            full_width=True,
            on_click=lambda _, identifier=record.id: on_select(identifier),
        )
        buttons[record.id] = button
        selected = ' aria-current="true"' if record.id == selected_id else ""
        muted = (
            " record-context"
            if matching_ids is not None and record.id not in matching_ids
            else ""
        )
        fragments.append(
            f'<li><div class="record-row{muted}"{selected}>{button.text}</div>'
        )
        nested = [
            child for child in children.get(record.id, []) if child.id not in buttons
        ]
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


def _filter_chip(
    label: str,
    active: bool,
    on_toggle: Callable[[], Any],
    *,
    disabled: bool = False,
    tooltip: str | None = None,
) -> mo.ui.button:
    return mo.ui.button(
        label=(
            f'<span class="record-filter-chip{" active" if active else ""}">'
            f"{'✓ ' if active else ''}{html.escape(label)}</span>"
        ),
        disabled=disabled,
        tooltip=tooltip,
        on_click=lambda _: on_toggle(),
    )


def record_filters(
    records: Sequence[Record],
    on_toggle: Callable[[str, str], Any],
    *,
    types: Sequence[str] = (),
    states: Sequence[str] = (),
) -> tuple[mo.Html, dict[tuple[str, str], mo.ui.button]]:
    """Render type/state toggle chips with caller-owned filter state.

    Return live button references. Each callback identifies the field and value
    to toggle; available values always come from the unfiltered record list.
    """
    buttons = {}
    groups = []
    for field, selected in (("type", types), ("state", states)):
        chips = []
        for value in sorted(
            {getattr(record, field) for record in records if getattr(record, field)}
        ):
            active = value in selected
            button = _filter_chip(
                value,
                active,
                lambda field=field, value=value: on_toggle(field, value),
                tooltip=f"{'Remove' if active else 'Add'} {field} filter: {value}",
            )
            buttons[field, value] = button
            chips.append(button.text)
        if chips:
            groups.append(
                f'<div class="record-filter-group" role="group" '
                f'aria-label="{field.title()} filters">'
                f'<span class="record-filter-label">{field.title()}</span>'
                f"{''.join(chips)}</div>"
            )
    return mo.Html(f'<div class="record-filters">{"".join(groups)}</div>'), buttons


def leaderboard_filters(
    active: frozenset[str],
    has_record: bool,
    run_count: int,
    on_toggle: Callable[[str], Any],
) -> tuple[mo.Html, dict[str, mo.ui.button]]:
    """Render leaderboard filter chips and the filtered run count."""
    buttons = {}
    for name, label in (
        ("record", "Selected record"),
        ("unattributed", "Include unattributed"),
    ):
        disabled = not has_record and name == "record"
        enabled = name in active and not disabled
        buttons[name] = _filter_chip(
            label,
            enabled,
            lambda name=name: on_toggle(name),
            disabled=disabled,
        )
    count_label = "run" if run_count == 1 else "runs"
    return mo.Html(
        '<div class="record-filter-group leaderboard-filters" role="group" '
        'aria-label="Leaderboard filters">'
        '<span class="record-filter-label">Filter</span>'
        + "".join(button.text for button in buttons.values())
        + f'<span class="leaderboard-run-count">{run_count:,} {count_label}</span>'
        + "</div>"
    ), buttons


def filesystem_tree(
    records: Sequence[Record],
    selected_id: str | None,
    on_select: Callable[[str], Any],
    *,
    types: Sequence[str] = (),
    states: Sequence[str] = (),
) -> tuple[mo.Html, dict[str, mo.ui.button]]:
    """Navigate bundle-relative Markdown paths, independently of record lineage.

    Filters match local type and state fields; empty filters include all values.
    Directories retain matching descendants and do not select records. Keep the
    returned buttons alive and supply selection state from the caller.
    """
    children: dict[str, set[tuple[str, bool]]] = {"": set()}
    by_id = {}
    for record in records:
        if (types and record.type not in types) or (
            states and record.state not in states
        ):
            continue
        by_id[record.id] = record
        parts = record.id.split("/")
        parent = ""
        for part in parts[:-1]:
            directory = f"{parent}/{part}" if parent else part
            children.setdefault(parent, set()).add((directory, True))
            children.setdefault(directory, set())
            parent = directory
        children[parent].add((record.id, False))

    buttons: dict[str, mo.ui.button] = {}
    fragments = ["<ul>"]
    stack: list[tuple[str, bool] | str] = list(
        reversed(sorted(children[""], key=lambda item: (not item[1], item[0])))
    )
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            fragments.append(node)
            continue
        path, directory = node
        label = html.escape(path.rsplit("/", 1)[-1])
        if directory:
            fragments.append(
                "<li><details open><summary>"
                f'<span class="record-folder-name">{label}</span></summary><ul>'
            )
            stack.append("</ul></details></li>")
            stack.extend(
                reversed(
                    sorted(children[path], key=lambda item: (not item[1], item[0]))
                )
            )
            continue
        record = by_id[path]
        button = mo.ui.button(
            label=_record_label(record),
            full_width=True,
            on_click=lambda _, identifier=path: on_select(identifier),
        )
        buttons[path] = button
        selected = ' aria-current="true"' if path == selected_id else ""
        fragments.append(
            f'<li><div class="record-row"{selected}>{button.text}</div></li>'
        )
    fragments.append("</ul>")
    content = "".join(fragments) if buttons else "No matching records."
    return (
        mo.Html(
            '<nav class="record-tree filesystem-tree" aria-label="Record files">'
            f"{content}</nav>"
        ),
        buttons,
    )


def lineage_tree(
    records: Sequence[Record],
    selected_id: str | None,
    on_select: Callable[[str], Any],
    *,
    types: Sequence[str] = (),
    states: Sequence[str] = (),
) -> tuple[mo.Html, dict[str, mo.ui.button]]:
    """Render matching records and their ancestors in lineage order."""
    return record_tree(
        lineage_records(records, types=types, states=states),
        selected_id,
        on_select,
        matching_ids={
            record.id
            for record in records
            if (not types or record.type in types)
            and (not states or record.state in states)
        },
    )


def plot_axes(
    metrics: Sequence[str],
    x_metric: str | None,
    y_metric: str | None,
    set_axes: Callable[..., None],
) -> tuple[mo.ui.dropdown, mo.ui.dropdown]:
    """Build axis selectors with callbacks into the caller's axis state."""
    run_order = "Run order"
    while run_order in metrics:
        run_order += " (execution)"
    return (
        mo.ui.dropdown(
            {run_order: None, **{name: name for name in metrics}},
            value=x_metric or run_order,
            label="X axis",
            on_change=lambda value: set_axes(lambda axes: (value, axes[1])),
        ),
        mo.ui.dropdown(
            metrics,
            value=y_metric,
            label="Y axis",
            on_change=lambda value: set_axes(lambda axes: (axes[0], value)),
        ),
    )


def run_table(
    records_df: pd.DataFrame,
    runs_df: pd.DataFrame,
    metric: str | None,
    initial_record: str | None = None,
    *,
    selected_key: str | None = None,
    on_select: Callable[[str | None], Any] | None = None,
    include_records: bool = True,
) -> mo.ui.table:
    """Build the selectable record/run table, retaining its hidden row keys."""
    _rows = tree_rows(records_df, runs_df, metric, include_records=include_records)
    metric_column = run_table_metric_column(metric)

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
        visible_columns=[
            *RUN_TABLE_COLUMNS,
            *([metric_column] if metric_column else []),
        ]
        if _rows
        else None,
        page_size=50,
        show_column_summaries=False,
        show_data_types=False,
        show_download=False,
        format_mapping={
            metric_column: (
                lambda value: f"{value:.4g}" if isinstance(value, float) else value
            )
        }
        if metric_column
        else None,
        style_cell=lambda row, column, value: _style(_rows[int(row)], column),
    )
    return tree


def _run_link(run: Mapping[str, Any]) -> str:
    url = safe_url(run.get("url"))
    if not url:
        return ""
    source = html.escape(str(run.get("source") or "tracking backend"))
    return (
        f' <a class="run-link" href="{html.escape(url, quote=True)}" target="_blank" '
        f'rel="noopener noreferrer">Open in {source} ↗</a>'
    )


def run_details(
    selected_run: Mapping[str, Any] | None, *, show_link: bool = True
) -> mo.Html:
    """Render run provenance and a safe tracking link, or a selection prompt."""
    if selected_run is None:
        _content = mo.md("Select a run in the leaderboard to see its details.")
    else:
        _link = _run_link(selected_run) if show_link else ""
        _content = mo.Html(
            f'<div class="run-provenance">{_link}'
            f"<pre>{html.escape(run_text(selected_run))}</pre></div>"
        )
    return mo.as_html(_content)


def performance_plot(
    runs_df: pd.DataFrame,
    *,
    x_metric: str | None = None,
    y_metric: str | None,
    directions: Mapping[str, str | None] | None = None,
    selected_record_id: str | None = None,
    selected_run_id: str | None = None,
    on_run_select: Callable[[str], Any] | None = None,
) -> mo.Html:
    """Plot a metric against execution order or another metric."""
    if y_metric is None:
        return mo.md("No numeric metrics are available to plot.")
    if x_metric == y_metric:
        return mo.callout("Choose two different metrics.", kind="info")

    tooltip = [
        alt.Tooltip("run:N", title="Run"),
        alt.Tooltip("record:N", title="Record"),
    ]
    if x_metric is None:
        points, _ = plot_frame(runs_df, y_metric)
        x = alt.X(
            "number:Q",
            title="Run number (execution order)",
            axis=alt.Axis(format="d", tickMinStep=1),
            scale=alt.Scale(nice=False, padding=16),
        )
        y = alt.Y("value:Q", title=y_metric, scale=alt.Scale(zero=False))
        tooltip.extend(
            [
                alt.Tooltip("started:N", title="Started"),
                alt.Tooltip("duration:N", title="Duration"),
                alt.Tooltip("value:Q", title=y_metric, format=".4g"),
            ]
        )
    else:
        points = tradeoff_frame(runs_df, x_metric, y_metric)
        directions = directions or {}
        x = alt.X(
            "x:Q",
            title=f"{x_metric} ({directions.get(x_metric) or 'minimize'})",
            scale=alt.Scale(zero=False),
        )
        y = alt.Y(
            "y:Q",
            title=f"{y_metric} ({directions.get(y_metric) or 'minimize'})",
            scale=alt.Scale(zero=False),
        )
        tooltip.extend(
            [
                alt.Tooltip("x:Q", title=x_metric, format=".4g"),
                alt.Tooltip("y:Q", title=y_metric, format=".4g"),
            ]
        )
    if points.empty:
        return mo.md("No runs to plot.")

    base = alt.Chart().encode(x=x, y=y)
    is_selected = alt.datum.run == selected_run_id
    dots = base.mark_circle(
        filled=True,
        opacity=1,
        stroke="#243449",
        cursor="pointer" if on_run_select is not None else alt.Undefined,
    ).encode(
        color=_run_color(selected_record_id),
        size=alt.condition(is_selected, alt.value(220), alt.value(90)),
        strokeWidth=alt.condition(is_selected, alt.value(1.5), alt.value(0)),
        tooltip=tooltip,
    )
    if on_run_select is not None:
        dots = dots.add_params(_run_selection())
    if x_metric is None:
        line = base.mark_line(strokeWidth=2, opacity=0.3)
        chart = alt.layer(line, dots, data=points)
    else:
        chart = dots.properties(data=points).interactive(name="pan_zoom")
    chart = (
        chart.properties(width="container", height=340)
        .configure(background="transparent")
        .configure_view(stroke=None)
    )
    return mo.vstack([_interactive_run_chart(chart, on_run_select)])


def _run_color(record_id: str | None):
    default = alt.value("#4c78a8")
    if record_id is None:
        return default
    return alt.condition(alt.datum.record == record_id, alt.value("#f59e0b"), default)


def _run_selection() -> alt.Parameter:
    return alt.selection_point(name="picked_run", fields=["run"], toggle=False)


def _interactive_run_chart(
    chart: alt.Chart | alt.LayerChart, on_run_select: Callable[[str], Any] | None
) -> alt.Chart | alt.LayerChart | mo.ui.altair_chart:
    if on_run_select is None:
        return chart

    def select(rows: pd.DataFrame) -> None:
        if not rows.empty:
            on_run_select(rows["run"].iloc[0])

    return mo.ui.altair_chart(
        chart, chart_selection=False, legend_selection=False, on_change=select
    )
