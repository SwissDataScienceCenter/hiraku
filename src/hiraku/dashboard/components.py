"""Reusable building blocks for the dashboard notebook.

Notebook cells stay short by delegating loading and rendering here, so a
project copy of ``app.py`` only describes what is shown and where.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from html import escape
from typing import Any

import altair as alt
import marimo as mo
import pandas as pd

from hiraku import HirakuConfig, Record, Run
from hiraku.errors import HirakuError
from hiraku.io import dump_yaml
from hiraku.record import list_records

_ERRORS = (HirakuError, OSError, ValueError, TypeError)

# Button labels live in shadow roots, so their styles belong in the label.
_BUTTON_STYLE = """<style>
  button {justify-content:flex-start !important;height:auto !important;
    min-height:32px;padding:4px 10px !important;border:0 !important;
    background:transparent !important;box-shadow:none !important;
    color:inherit !important;}
  .paragraph {display:flex;align-items:center;flex-wrap:wrap;gap:7px;
    margin:0 !important;line-height:1.4;}
  .record-id {font:13px ui-monospace,monospace;overflow-wrap:anywhere;}
  .badge {font:10px/1.6 system-ui,sans-serif;text-transform:uppercase;
    background:#f1f3f2;border-radius:12px;padding:1px 7px;color:#61716d;
    white-space:nowrap;}
  .dot {width:7px;height:7px;background:#94a3b8;border-radius:50%;
    flex-shrink:0;}
  button:focus-visible {outline:2px solid #0f766e !important;}
</style>"""
_TREE_STYLE = """<style>
  .record-tree ul {list-style:none;margin:0;padding:0;}
  .record-tree ul ul {margin-left:18px;padding-left:10px;
    border-left:1px solid #e2e8f0;}
  .record-row {margin:1px 0;border:1px solid transparent;border-radius:7px;}
  .record-row:hover {background:#f8fafc;}
  .record-row.selected {background:#f0fdfa;border-color:#0f766e;}
</style>"""


def numeric(value: Any) -> float | None:
    """Return a finite float, or None for booleans and non-numeric values."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def load_project() -> tuple[HirakuConfig | None, list[Record], str | None, str]:
    """Discover the project and return its config, records, initial id, error."""
    try:
        config = HirakuConfig.discover()
        records = list_records(config=config)
    except _ERRORS as exc:
        return None, [], None, str(exc)
    ids = {record.id for record in records}
    initial_id = config.active_record_id
    if initial_id not in ids:
        initial_id = next((r.id for r in records if r.parent_id is None), None)
    return config, records, initial_id, ""


def load_runs(
    record: Record | None, config: HirakuConfig | None
) -> tuple[list[Any], list[Run], str]:
    """Return a record's metric declarations, tracked runs, and any error."""
    if record is None:
        return [], [], ""
    try:
        declarations = record.resolve(config=config).values.get("metrics") or []
        runs = record.resolve_tracking_backend(config=config).list_runs()
    except (*_ERRORS, ImportError) as exc:
        return [], [], str(exc)
    return declarations, runs, ""


def record_tree(
    records: Sequence[Any], selected_id: str | None, on_select: Callable[[str], Any]
) -> tuple[mo.Html, dict[str, mo.ui.button]]:
    """Render the record hierarchy and return it with its per-record buttons.

    Keep the returned buttons in a notebook variable so they stay interactive
    while their HTML is nested inside the tree.
    """
    buttons: dict[str, mo.ui.button] = {}
    children: dict[str | None, list[Any]] = {}
    for item in records:
        children.setdefault(item.parent_id, []).append(item)
    fragments = []
    stack = [(item, False) for item in reversed(children.get(None, []))]
    if stack:
        fragments.append("<ul>")
    while stack:
        item, closing = stack.pop()
        if closing:
            fragments.append("</ul></li>")
            continue
        badges = " ".join(
            f'<span class="badge">{escape(value)}</span>'
            for value in (item.type, item.status, item.decision)
            if value
        )
        button = mo.ui.button(
            label=(
                _BUTTON_STYLE + '<span class="dot"></span>'
                f'<span class="record-id">{escape(item.id)}</span>{badges}'
            ),
            full_width=True,
            on_click=lambda _, identifier=item.id: on_select(identifier),
        )
        buttons[item.id] = button
        selected = " selected" if item.id == selected_id else ""
        fragments.append(f'<li><div class="record-row{selected}">{button.text}</div>')
        nested = children.get(item.id, [])
        if nested:
            fragments.append("<ul>")
            stack.append((item, True))
            stack.extend((child, False) for child in reversed(nested))
        else:
            fragments.append("</li>")
    if fragments:
        fragments.append("</ul>")
    tree = mo.Html(f'{_TREE_STYLE}<div class="record-tree">{"".join(fragments)}</div>')
    return tree, buttons


def record_details(record: Record) -> mo.Html:
    """Render a record's id, front matter, and Markdown body."""
    return mo.vstack(
        [
            mo.Html(f"<h1>{escape(record.id)}</h1>"),
            mo.Html(
                '<pre class="front-matter" aria-label="Record front matter">'
                f"{escape(dump_yaml(record.to_dict()))}</pre>"
            ),
            mo.md(record.markdown or "This record has no Markdown."),
        ]
    )


def metric_names(declarations: Sequence[Any], runs: Sequence[Run]) -> list[str]:
    """List declared metrics first, then other numeric run metrics."""
    observed = {
        name
        for run in runs
        for name, value in run.metrics.items()
        if numeric(value) is not None
    }
    return list(dict.fromkeys([*(m.name for m in declarations), *sorted(observed)]))


def metric_direction(declarations: Sequence[Any], metric: str | None) -> str | None:
    """Return the declared optimization direction of a metric, if any."""
    return next((m.direction for m in declarations if m.name == metric), None)


def run_frame(
    runs: Sequence[Run],
    metric: str | None,
    selected_id: str | None = None,
    direction: str | None = None,
) -> pd.DataFrame:
    """Tabulate runs with a usable value for ``metric``, one row per run.

    ``position`` keeps the run's index in ``runs``. With a ``direction``, the
    ``best`` column holds the running minimum or maximum.
    """
    rows = []
    for position, run in enumerate(runs, start=1):
        value = numeric(run.metrics.get(metric))
        if value is None:
            continue
        rows.append(
            {
                "position": position,
                "id": run.id,
                "record_id": run.record_id,
                "value": value,
                "selected": run.record_id == selected_id,
                "timestamp": run.timestamp.isoformat() if run.timestamp else "",
                "entrypoint": run.entrypoint or "",
                "commit": run.commit or "",
                "params": json.dumps(dict(run.params), default=str),
                "url": run.url or "",
            }
        )
    frame = pd.DataFrame(rows)
    if rows and direction:
        values = frame.value
        frame["best"] = values.cummin() if direction == "minimize" else values.cummax()
    return frame


def run_chart(
    frame: pd.DataFrame, metric: str, on_select: Callable[[str], Any]
) -> mo.ui.altair_chart:
    """Plot ``run_frame`` output; clicking a run selects its record."""
    base = alt.Chart(frame).encode(
        x=alt.X("position:Q", title="Run", axis=alt.Axis(tickMinStep=1)),
        y=alt.Y("value:Q", title=metric, scale=alt.Scale(zero=False)),
    )
    selected = alt.datum.selected == True  # noqa: E712
    chart = (
        base.mark_circle(size=85)
        .encode(
            color=alt.condition(selected, alt.value("#dc2626"), alt.value("#94a3b8")),
            opacity=alt.condition(selected, alt.value(1), alt.value(0.6)),
            tooltip=[
                alt.Tooltip("id:N", title="Run"),
                alt.Tooltip("record_id:N", title="Record"),
                alt.Tooltip("value:Q", title=metric),
                "timestamp:N",
                "entrypoint:N",
                "commit:N",
                "params:N",
                "url:N",
            ],
        )
        .add_params(
            alt.selection_point(
                fields=["id", "record_id"], nearest=True, toggle=False, clear=False
            )
        )
    )
    if "best" in frame:
        line = base.mark_line(interpolate="step-after", color="#0f766e")
        chart = line.encode(y="best:Q") + chart

    def on_change(selection: pd.DataFrame) -> None:
        if not selection.empty:
            on_select(selection.iloc[0]["record_id"])

    alt.data_transformers.register(
        "marimo_inline", lambda data: {"values": data.to_dict("records")}
    )
    with alt.data_transformers.enable("marimo_inline"):
        return mo.ui.altair_chart(
            chart.properties(width="container", height=260).configure_view(stroke=None),
            chart_selection=False,
            legend_selection=False,
            on_change=on_change,
        )
