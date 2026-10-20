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
app = marimo.App()


@app.cell
def _():
    import html
    import os
    from pathlib import Path

    import altair as alt
    import marimo as mo

    from hiraku.config import HirakuConfig
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
        tradeoff_frame,
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
    return (
        alt,
        arrow_frame,
        config,
        default_metric,
        find_run,
        html,
        load,
        metric_names,
        mo,
        tradeoff_frame,
        plot_frame,
        RECORD_COLUMNS,
        record_rows,
        requested_record,
        RUN_COLUMNS,
        run_rows,
        run_text,
        safe_url,
        subtree,
        tree_rows,
        within,
    )


@app.cell
def project_name(config, mo):
    mo.plain_text(config.root.name)
    return


@app.cell
def refresh_button(mo):
    refresh = mo.ui.button(label="Refresh", value=0, on_click=lambda count: count + 1)
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
def tree_section(
    initial_record,
    mo,
    project,
    records_df,
    runs_df,
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
        hidden_columns=["key", "depth"] if _rows else None,
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
    mo.vstack([*_messages, tree])
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
def run_section(
    html,
    mo,
    run_text,
    safe_url,
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
                mo.Html(f"<pre>{html.escape(run_text(selected_run))}</pre>"),
            ]
        )
    mo.as_html(_content)
    return


@app.cell
def _(metrics, mo, records_df, tree_metric):
    plot_mode = mo.ui.dropdown(
        ["Performance over runs", "Metric trade-offs"],
        value="Performance over runs",
        label="View",
    )
    metric = mo.ui.dropdown(metrics, value=tree_metric, label="Metric (y axis)")
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


@app.cell
def plot_section(
    alt,
    metric,
    mo,
    paths,
    plot_frame,
    runs_df,
    scope,
    selected_run,
    subtree,
    tradeoff_frame,
    plot_mode,
    x_metric,
    x_objective,
    y_metric,
    y_objective,
):
    _controls = [plot_mode]
    _scoped = subtree(runs_df, paths.get(scope))
    if plot_mode.value == "Performance over runs":
        _controls.append(metric)
        _points, _missing = plot_frame(_scoped, metric.value)
        _notes = [
            f"{len(_scoped)} runs under **{scope}**, numbered by execution order."
        ]
        if _missing:
            _notes.append(
                f"{_missing} without a numeric `{metric.value}` are not shown."
            )
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
            _line = _base.mark_line(strokeWidth=2, opacity=0.3)
            _dots = _base.mark_point(filled=True, size=90, opacity=1).encode(
                color=alt.Color("record:N", title="Record"),
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
        _content = mo.vstack([*_controls, mo.md(" ".join(_notes)), _chart])
    else:
        _controls.extend([x_metric, x_objective, y_metric, y_objective])
        if not x_metric.value or not y_metric.value:
            _comparison = mo.md("No numeric metrics are available to compare.")
        elif x_metric.value == y_metric.value:
            _comparison = mo.callout("Choose two different metrics.", kind="info")
        else:
            _points, _missing = tradeoff_frame(
                _scoped,
                x_metric.value,
                y_metric.value,
                x_objective.value,
                y_objective.value,
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
                        title=f"{x_metric.value} ({x_objective.value})",
                        scale=alt.Scale(zero=False),
                    ),
                    y=alt.Y(
                        "y:Q",
                        title=f"{y_metric.value} ({y_objective.value})",
                        scale=alt.Scale(zero=False),
                    ),
                    color=alt.Color("record:N", title="Record"),
                    tooltip=[
                        alt.Tooltip("run:N", title="Run"),
                        alt.Tooltip("record:N", title="Record"),
                        alt.Tooltip("x:Q", title=x_metric.value, format=".4g"),
                        alt.Tooltip("y:Q", title=y_metric.value, format=".4g"),
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
                        "x": x_metric.value,
                        "y": y_metric.value,
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
        _content = mo.vstack([*_controls, _comparison])
    mo.as_html(_content)
    return


if __name__ == "__main__":
    app.run()
