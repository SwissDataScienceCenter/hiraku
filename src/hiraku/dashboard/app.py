# /// script
# [tool.marimo.display]
# theme = "system"
# ///
"""Bundled Hiraku dashboard. Start it with ``hiraku dashboard``.

``hiraku dashboard --init`` copies this notebook into the project and
``hiraku dashboard --edit`` opens that copy in the marimo editor.
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
        arrow_frame,
        backend_label,
        children,
        default_metric,
        load,
        metric_names,
        plot_points,
        record_rows,
        run_rows,
        run_text,
        safe_url,
        subtree_ids,
        tree_rows,
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
        backend_label,
        children,
        config,
        default_metric,
        html,
        load,
        logo_data_uri,
        metric_names,
        mo,
        palette,
        plot_points,
        record_rows,
        requested_record,
        run_rows,
        run_text,
        safe_url,
        section_body,
        section_heading,
        subtree_ids,
        theme_css,
        tree_rows,
    )


@app.cell
def _(config, logo_data_uri, mo, theme_css):
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
def _(mo):
    refresh = mo.ui.button(label="↻ Refresh", value=0, on_click=lambda count: count + 1)
    mo.hstack([refresh], justify="end")
    return (refresh,)


@app.cell
def _(children, config, default_metric, load, mo, refresh, requested_record):
    _refreshes = refresh.value  # rerun this cell when Refresh is clicked
    project = load(config)
    runs_by_id = {run.id: run for run in project.runs}
    tree_metric = default_metric(project.records, project.runs)
    roots = children(project.records).get(None, [])
    known = {record.id for record in project.records}
    initial_record = next(
        (
            record_id
            for record_id in (requested_record, config.active_record_id)
            if record_id in known
        ),
        roots[0] if roots else None,
    )
    get_scope, set_scope = mo.state(initial_record)
    return get_scope, initial_record, project, runs_by_id, set_scope, tree_metric


@app.cell
def _(arrow_frame, project, record_rows, run_rows):
    # Arrow-backed tables for your own cells or marimo's AI assistant (mention
    # @runs_df and @records_df in its chat). runs_df has one row per run with
    # metric.* and param.* columns; record_path places runs and records in the
    # tree, and records_df lists each record's declared metrics.
    runs_df = arrow_frame(run_rows(project))
    records_df = arrow_frame(record_rows(project.records))
    return records_df, runs_df


@app.cell
def _(section_heading):
    section_heading("tree", "Runs", open=True)
    return


@app.cell
def _(initial_record, mo, project, section_body, tree_metric, tree_rows):
    _rows = tree_rows(project.records, project.runs, tree_metric)

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
    if not project.records:
        _messages.append(
            mo.md("No records yet. Create one with `hiraku record create`.")
        )
    section_body("tree", mo.vstack([*_messages, tree]))
    return (tree,)


@app.cell
def _(get_scope, project, runs_by_id, set_scope, subtree_ids, tree):
    _key = tree.value[0]["key"] if tree.value else f"record:{get_scope()}"
    _kind, _, _identifier = _key.partition(":")
    selected_run = runs_by_id.get(_identifier) if _kind == "run" else None
    scope = _identifier if _kind == "record" else get_scope()
    # Keep comparing against the chosen record unless the run lies outside it.
    if selected_run is not None and (
        scope is None
        or selected_run.record_id not in subtree_ids(project.records, scope)
    ):
        scope = selected_run.record_id
    set_scope(scope)
    scoped_ids = subtree_ids(project.records, scope) if scope else set()
    scoped_runs = [run for run in project.runs if run.record_id in scoped_ids]
    return scope, scoped_runs, selected_run


@app.cell
def _(section_heading):
    section_heading("run", "Run")
    return


@app.cell
def _(
    backend_label,
    html,
    mo,
    project,
    run_text,
    safe_url,
    section_body,
    selected_run,
):
    if selected_run is None:
        _content = mo.md("Select a run in the tree to see its details.")
    else:
        _url = safe_url(selected_run.url)
        _label = backend_label(project.sources.get(selected_run.id))
        _content = mo.vstack(
            [
                mo.md(f"[{_label} ↗]({_url})") if _url else mo.md(""),
                mo.Html(
                    f'<pre class="hk-run-text">{html.escape(run_text(selected_run))}'
                    "</pre>"
                ),
            ]
        )
    section_body("run", _content)
    return


@app.cell
def _(section_heading):
    section_heading("plot", "Performance")
    return


@app.cell
def _(metric_names, mo, project, tree_metric):
    metric = mo.ui.dropdown(
        metric_names(project.runs), value=tree_metric, label="Metric (y axis)"
    )
    return (metric,)


@app.cell
def _(
    alt,
    metric,
    mo,
    palette,
    plot_points,
    scope,
    scoped_runs,
    section_body,
    selected_run,
):
    _points, _missing = plot_points(scoped_runs, metric.value)
    _notes = [
        f"{len(scoped_runs)} runs under **{scope}**, numbered by execution order."
    ]
    if _missing:
        _notes.append(f"{_missing} without a numeric `{metric.value}` are not shown.")
    if not _points:
        _chart = mo.md("No runs to plot.")
    else:
        _base = alt.Chart(alt.Data(values=_points)).encode(
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
                _base.transform_filter(alt.datum.run == selected_run.id).mark_point(
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
def _(logo_data_uri, mo):
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
