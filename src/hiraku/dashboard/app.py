# /// script
# [tool.marimo.display]
# theme = "system"
#
# [tool.marimo-studio]
# default = "dashboard"
# view_root = "studio"
# ///
"""Bundled Hiraku dashboard. Start it with ``hiraku dashboard``.

``hiraku dashboard --init`` copies this notebook into the project and
``hiraku dashboard --edit`` opens that copy in marimo-studio.
"""

import marimo

__generated_with = "0.25.1"
app = marimo.App(width="wide")


@app.cell
def _():
    import html
    import os
    from pathlib import Path

    import altair as alt
    import marimo as mo

    from hiraku.config import HirakuConfig
    from hiraku.dashboard.branding import (
        logo_data_uri,
        section_body,
        section_heading,
        theme_css,
    )
    from hiraku.dashboard.data import (
        RECORD_COLUMNS,
        RUN_COLUMNS,
        arrow_frame,
        default_metric,
        find_run,
        load,
        metric_names,
        plot_frame,
        record_rows,
        run_rows,
        run_text,
        safe_url,
        subtree,
        tree_rows,
        within,
    )

    _root = os.environ.get("HIRAKU_PROJECT_ROOT")
    # A project copy opened with plain `marimo edit` finds its own project.
    config = (
        HirakuConfig.load(Path(_root) / "hiraku.yml")
        if _root
        else HirakuConfig.discover(mo.notebook_dir())
    )
    requested_record = os.environ.get("HIRAKU_DASHBOARD_RECORD") or None
    # Brand colours from theme.css that stay visible on light and dark pages;
    # the warning orange is reserved for the selected-run ring.
    palette = ["#5461a6", "#90ca42", "#848484", "#3b82f6", "#ef4444", "#10b981"]
    return (
        alt,
        arrow_frame,
        config,
        default_metric,
        find_run,
        html,
        load,
        logo_data_uri,
        metric_names,
        mo,
        palette,
        plot_frame,
        RECORD_COLUMNS,
        record_rows,
        requested_record,
        RUN_COLUMNS,
        run_rows,
        run_text,
        safe_url,
        section_body,
        section_heading,
        subtree,
        theme_css,
        tree_rows,
        within,
    )


@app.cell
def header(config, logo_data_uri, mo, theme_css):
    mo.Html(
        f"<style>{theme_css()}</style>"
        '<header class="hk-header"><div class="hk-shell hk-header-inner">'
        f'<img class="hk-brand-logo hk-logo-light" src="{logo_data_uri()}" '
        'alt="Swiss Data Science Center">'
        f'<img class="hk-brand-logo hk-logo-dark" '
        f'src="{logo_data_uri(reversed_logo=True)}" alt="Swiss Data Science Center">'
        '<div><h1 class="hk-brand-title">Hiraku dashboard</h1>'
        '<p class="hk-brand-subtitle">Research records and tracked runs · '
        f"{config.root.name}</p></div></div></header>"
    )
    return


@app.cell
def refresh_button(mo):
    refresh = mo.ui.button(label="↻ Refresh", value=0, on_click=lambda count: count + 1)
    mo.hstack([refresh], justify="end")
    return (refresh,)


@app.cell
def _(
    RECORD_COLUMNS,
    RUN_COLUMNS,
    arrow_frame,
    config,
    load,
    record_rows,
    refresh,
    run_rows,
):
    _refreshes = refresh.value  # rerun this cell when Refresh is clicked
    # Read the records and the runs from each record's tracking backend, and
    # flatten them into the two Arrow-backed tables every view below reads.
    # runs_df: one row per run, metric.* and param.* columns. records_df: one
    # row per record with its declared metrics. record_path places both in the
    # tree. Mention @runs_df and @records_df in marimo's AI assistant to ask
    # questions about them.
    project = load(config)
    runs_df = arrow_frame(run_rows(project), RUN_COLUMNS)
    records_df = arrow_frame(record_rows(project.records), RECORD_COLUMNS)
    return project, records_df, runs_df


@app.cell
def _(
    config,
    default_metric,
    metric_names,
    mo,
    records_df,
    requested_record,
    runs_df,
):
    paths = dict(zip(records_df["record"], records_df["record_path"], strict=True))
    metrics = metric_names(runs_df)
    tree_metric = default_metric(records_df, metrics)
    initial_record = next(
        (
            record_id
            for record_id in (requested_record, config.active_record_id)
            if record_id in paths
        ),
        next(iter(paths), None),
    )
    get_scope, set_scope = mo.state(initial_record)
    return get_scope, initial_record, metrics, paths, set_scope, tree_metric


@app.cell
def tree_heading(section_heading):
    section_heading("tree", "Runs", open=True)
    return


@app.cell
def tree_section(
    initial_record,
    mo,
    project,
    records_df,
    runs_df,
    section_body,
    tree_metric,
    tree_rows,
):
    _rows = tree_rows(records_df, runs_df, tree_metric)

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
        hidden_columns=["key", "depth"],
        page_size=50,
        show_column_summaries=False,
        show_data_types=False,
        show_download=False,
        format_mapping={
            tree_metric: lambda value: (
                f"{value:.4g}" if isinstance(value, float) else value
            )
        }
        if tree_metric
        else None,
        style_cell=lambda row, column, value: _style(_rows[int(row)], column),
    )
    _messages = [mo.callout(mo.md(error), kind="warn") for error in project.errors]
    if records_df.empty:
        _messages.append(
            mo.md("No records yet. Create one with `hiraku record create`.")
        )
    section_body("tree", mo.vstack([*_messages, tree]))
    return (tree,)


@app.cell
def _(find_run, get_scope, paths, runs_df, set_scope, tree, within):
    _key = tree.value[0]["key"] if tree.value else f"record:{get_scope()}"
    _kind, _, _identifier = _key.partition(":")
    selected_run = find_run(runs_df, _identifier) if _kind == "run" else None
    scope = _identifier if _kind == "record" else get_scope()
    # Keep comparing against the chosen record unless the run lies outside it.
    if selected_run is not None and (
        scope is None or not within(selected_run["record_path"], paths[scope])
    ):
        scope = selected_run["record"]
    set_scope(scope)
    return scope, selected_run


@app.cell
def run_heading(section_heading):
    section_heading("run", "Run")
    return


@app.cell
def run_section(
    html,
    mo,
    run_text,
    safe_url,
    section_body,
    selected_run,
):
    if selected_run is None:
        _content = mo.md("Select a run in the tree to see its details.")
    else:
        # The link and the source name come from the run's tracking backend.
        _url = safe_url(selected_run["url"])
        _source = selected_run["source"] or "tracking backend"
        _content = mo.vstack(
            [
                mo.md(f"[Open in {_source} ↗]({_url})") if _url else mo.md(""),
                mo.Html(
                    f'<pre class="hk-run-text">{html.escape(run_text(selected_run))}'
                    "</pre>"
                ),
            ]
        )
    section_body("run", _content)
    return


@app.cell
def plot_heading(section_heading):
    section_heading("plot", "Performance")
    return


@app.cell
def _(metrics, mo, tree_metric):
    metric = mo.ui.dropdown(metrics, value=tree_metric, label="Metric (y axis)")
    return (metric,)


@app.cell
def plot_section(
    alt,
    metric,
    mo,
    palette,
    paths,
    plot_frame,
    runs_df,
    scope,
    section_body,
    selected_run,
    subtree,
):
    # The runs under the selected record, numbered in execution order.
    _scoped = subtree(runs_df, paths.get(scope))
    _points, _missing = plot_frame(_scoped, metric.value)
    _notes = [f"{len(_scoped)} runs under **{scope}**, numbered by execution order."]
    if _missing:
        _notes.append(f"{_missing} without a numeric `{metric.value}` are not shown.")
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
            y=alt.Y("value:Q", title=metric.value, scale=alt.Scale(zero=False)),
        )
        _line = _base.mark_line(color="#dddeec", strokeWidth=2)
        _dots = _base.mark_point(filled=True, size=90, opacity=1).encode(
            color=alt.Color("record:N", scale=alt.Scale(range=palette), title="Record"),
            tooltip=[
                alt.Tooltip("number:Q", title="Run #"),
                alt.Tooltip("run:N", title="Run"),
                alt.Tooltip("record:N", title="Record"),
                alt.Tooltip("started:N", title="Started"),
                alt.Tooltip("value:Q", title=metric.value, format=".4g"),
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
            .configure_axis(labelColor="#848484", titleColor="#848484")
            .configure_legend(labelColor="#848484", titleColor="#848484")
            .configure_view(stroke=None)
        )
    section_body("plot", mo.vstack([metric, mo.md(" ".join(_notes)), _chart]))
    return


@app.cell
def footer(logo_data_uri, mo):
    mo.Html(
        '<footer class="hk-footer"><div class="hk-shell hk-footer-inner">'
        f'<img src="{logo_data_uri(reversed_logo=True)}" '
        'alt="Swiss Data Science Center">'
        '<p class="hk-footer-label">Swiss Data Science Center</p>'
        "</div></footer>"
    )
    return


if __name__ == "__main__":
    app.run()
