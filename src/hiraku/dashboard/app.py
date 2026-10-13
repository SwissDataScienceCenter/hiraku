# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "marimo==0.25.1", "marimo-studio==0.3.0", "altair>=5,<7", "pandas>=2,<3",
# ]
#
# [tool.marimo-studio]
# default = "dashboard"
# view_root = "views"
# runtime = "server"
# ///
"""Read-only browser using Hiraku's Python API.

From the project to browse:
    uv run --extra dashboard hiraku dashboard

Try a populated project with `hiraku init --example heart-disease --results`.
Reload to read updated records. Edit run_plot to modify the entire chart.
"""

import marimo

__generated_with = "0.25.1"
app = marimo.App()


@app.cell
def imports():
    import json
    import math
    from html import escape

    import altair as alt
    import marimo as mo
    import pandas as pd

    from hiraku import HirakuConfig
    from hiraku.errors import HirakuError
    from hiraku.io import dump_yaml
    from hiraku.record import list_records

    def numeric(value):
        if isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if math.isfinite(number) else None

    return (
        HirakuConfig,
        HirakuError,
        alt,
        dump_yaml,
        escape,
        json,
        list_records,
        mo,
        numeric,
        pd,
    )


@app.cell
def project_data(HirakuConfig, HirakuError, list_records):
    config, records, project_error = None, [], ""
    try:
        config = HirakuConfig.discover()
        records = list_records(config=config)
    except (HirakuError, OSError, ValueError, TypeError) as exc:
        project_error = str(exc)
    record_index = {record.id: record for record in records}
    initial_id = config.active_record_id if config else None
    if initial_id not in record_index:
        initial_id = next((r.id for r in records if r.parent_id is None), None)
    return config, initial_id, project_error, record_index, records


@app.cell
def selection_state(initial_id, mo, record_index):
    get_selection, set_selection = mo.state((initial_id, ""))
    get_metric, set_metric = mo.state(None, allow_self_loops=True)

    def select_record(identifier):
        if identifier in record_index:
            set_selection((identifier, ""))
        else:
            set_selection(
                lambda previous: (
                    previous[0],
                    f"Record {identifier!r} is not in this project.",
                )
            )

    return get_metric, get_selection, select_record, set_metric


@app.cell
def selected_record(get_selection, record_index):
    selected_id, selection_message = get_selection()
    record = record_index.get(selected_id)
    return record, selected_id, selection_message


@app.cell
def record_tree(escape, mo, project_error, records, select_record, selected_id):
    # Keep controls alive when their HTML is nested inside the tree.
    tree_buttons = {}
    # Button labels live in shadow roots, so their styles belong in the label.
    button_style = """<style>
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
    tree_style = """<style>
      .record-tree ul {list-style:none;margin:0;padding:0;}
      .record-tree ul ul {margin-left:18px;padding-left:10px;
        border-left:1px solid #e2e8f0;}
      .record-row {margin:1px 0;border:1px solid transparent;border-radius:7px;}
      .record-row:hover {background:#f8fafc;}
      .record-row.selected {background:#f0fdfa;border-color:#0f766e;}
    </style>"""

    def branch(parent_id):
        rows = []
        for item in records:
            if item.parent_id != parent_id:
                continue
            badges = " ".join(
                f'<span class="badge">{escape(value)}</span>'
                for value in (item.type, item.status, item.decision)
                if value
            )
            button = mo.ui.button(
                label=(
                    button_style + '<span class="dot"></span>'
                    f'<span class="record-id">{escape(item.id)}</span>{badges}'
                ),
                full_width=True,
                on_click=lambda _, identifier=item.id: select_record(identifier),
            )
            tree_buttons[item.id] = button
            selected = " selected" if item.id == selected_id else ""
            rows.append(
                f'<li><div class="record-row{selected}">{button.text}</div>'
                f"{branch(item.id)}</li>"
            )
        return "<ul>" + "".join(rows) + "</ul>" if rows else ""

    tree = (
        mo.Html(f'{tree_style}<div class="record-tree">{branch(None)}</div>')
        if records
        else mo.md(project_error or "No records found.")
    )
    mo.vstack([mo.md("## Records"), tree])
    return (tree_buttons,)


@app.cell
def record_details(dump_yaml, escape, mo, project_error, record):
    if record is None:
        details = mo.md(project_error or "No records found.")
    else:
        details = mo.vstack(
            [
                mo.Html(f"<h1>{escape(record.id)}</h1>"),
                mo.Html(
                    '<pre class="front-matter" aria-label="Record front matter">'
                    f"{escape(dump_yaml(record.to_dict()))}</pre>"
                ),
                mo.md(record.markdown or "This record has no Markdown."),
            ]
        )
    mo.output.replace(details)
    return


@app.cell
def tracking_data(HirakuError, config, record):
    runs, declarations, tracking_error = [], [], ""
    if record is not None:
        try:
            declarations = record.resolve(config=config).values.get("metrics") or []
            runs = record.resolve_tracking_backend(config=config).list_runs()
        except (HirakuError, OSError, ValueError, TypeError, ImportError) as exc:
            tracking_error = str(exc)
    return declarations, runs, tracking_error


@app.cell
def metric_control(declarations, get_metric, mo, numeric, runs, set_metric):
    metric_names = list(
        dict.fromkeys(
            [
                *(metric.name for metric in declarations),
                *sorted(
                    {
                        name
                        for run in runs
                        for name, value in run.metrics.items()
                        if numeric(value) is not None
                    }
                ),
            ]
        )
    )
    preferred = get_metric()
    metric_selector = mo.ui.dropdown(
        options=metric_names,
        value=(
            preferred if preferred in metric_names else next(iter(metric_names), None)
        ),
        label="Metric",
        on_change=set_metric,
    )
    metric_selector if metric_names else mo.md("No numeric metrics available.")
    return (metric_selector,)


@app.cell
def run_plot(
    alt,
    declarations,
    json,
    metric_selector,
    mo,
    numeric,
    pd,
    runs,
    select_record,
    selected_id,
    selection_message,
    tracking_error,
):
    # All chart construction and interaction live in this cell.
    metric_name = metric_selector.value
    rows = []
    for position, run in enumerate(runs, start=1):
        value = numeric(run.metrics.get(metric_name))
        if value is not None:
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
    plot_data = pd.DataFrame(rows)
    direction = next((m.direction for m in declarations if m.name == metric_name), None)

    def on_run_click(selection):
        if not selection.empty:
            identifier = selection.iloc[0]["record_id"]
            if identifier != selected_id:
                select_record(identifier)

    run_chart = None
    if rows:
        base = alt.Chart(plot_data).encode(
            x=alt.X("position:Q", title="Run", axis=alt.Axis(tickMinStep=1)),
            y=alt.Y("value:Q", title=metric_name, scale=alt.Scale(zero=False)),
        )
        click = alt.selection_point(
            fields=["id"], nearest=True, toggle=False, clear=False
        )
        dots = (
            base.mark_circle(size=85)
            .encode(
                color=alt.condition(
                    alt.datum.selected == True,  # noqa: E712
                    alt.value("#dc2626"),
                    alt.value("#94a3b8"),
                ),
                opacity=alt.condition(
                    alt.datum.selected == True,  # noqa: E712
                    alt.value(1),
                    alt.value(0.6),
                ),
                tooltip=[
                    alt.Tooltip("id:N", title="Run"),
                    alt.Tooltip("record_id:N", title="Record"),
                    alt.Tooltip("value:Q", title=metric_name),
                    "timestamp:N",
                    "entrypoint:N",
                    "commit:N",
                    "params:N",
                    "url:N",
                ],
            )
            .add_params(click)
        )
        chart = dots
        if direction:
            plot_data["best"] = (
                plot_data.value.cummin()
                if direction == "minimize"
                else plot_data.value.cummax()
            )
            line = base.mark_line(interpolate="step-after", color="#0f766e").encode(
                y="best:Q"
            )
            chart = line + dots
        alt.data_transformers.register(
            "marimo_inline", lambda data: {"values": data.to_dict("records")}
        )
        with alt.data_transformers.enable("marimo_inline"):
            run_chart = mo.ui.altair_chart(
                chart.properties(width="container", height=260).configure_view(
                    stroke=None
                ),
                chart_selection=False,
                legend_selection=False,
                on_change=on_run_click,
            )
    mo.vstack(
        [
            mo.md(selection_message) if selection_message else mo.Html(""),
            run_chart
            if run_chart is not None
            else mo.md(
                tracking_error or "No runs with a usable value for this metric."
            ),
        ]
    )
    return direction, on_run_click, plot_data, run_chart


if __name__ == "__main__":
    app.run()
