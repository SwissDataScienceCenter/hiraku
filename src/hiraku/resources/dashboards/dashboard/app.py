# /// script
# [tool.marimo.display]
# theme = "system"
#
# [tool.marimo-studio]
# default = "dashboard"
# view_root = "studio"
# ///
"""Dashboard wiring. Start with ``hiraku dashboard serve`` or edit a project copy.

Reusable UI lives in ``hiraku.dashboard.components``; the Studio view owns
branding and layout. Keep loading, selection, and reactive widget reads here.
"""

import marimo

__generated_with = "0.25.1"
app = marimo.App(css_file="studio/dashboard/components.css")


with app.setup:
    import os
    from pathlib import Path

    import marimo as mo

    from hiraku.dashboard import components as hc
    from hiraku.dashboard import data as hd
    from hiraku.project import Project
    from hiraku.tracking import Leaderboard


@app.cell
def data_setup():
    _root = os.environ.get("HIRAKU_PROJECT_ROOT")
    # Project copies opened directly in marimo discover their own project.
    project_context = (
        Project.discover(Path(_root)) if _root else Project.discover(mo.notebook_dir())
    )
    requested_record = os.environ.get("HIRAKU_DASHBOARD_RECORD") or None
    requested_leaderboard = os.environ.get("HIRAKU_DASHBOARD_LEADERBOARD") or None
    repository_url = hd.github_repository(project_context)
    return project_context, requested_record, requested_leaderboard, repository_url


@app.cell
def project_name(project_context):
    mo.plain_text(project_context.root.name)
    return


@app.cell
def refresh_control(project_context, requested_leaderboard):
    refresh = mo.ui.button(label="Refresh", value=0, on_click=lambda count: count + 1)
    _names = Leaderboard.list(project_context)
    leaderboard_selector = mo.ui.dropdown(
        _names,
        value=requested_leaderboard
        if requested_leaderboard in _names
        else (_names[0] if len(_names) == 1 else None),
        label='<span class="leaderboard-selector-label">Leaderboard</span>',
    )
    mo.hstack([refresh], justify="end")
    return refresh, leaderboard_selector


@app.cell
def project_data(project_context, refresh, leaderboard_selector):
    _refreshes = refresh.value
    records = project_context.vault.list()
    leaderboard = None
    leaderboard_error = None
    _runs = []
    try:
        leaderboard = Leaderboard.load(project_context, leaderboard_selector.value)
        _runs = leaderboard.list_runs(project_context, include_unattributed=True)
    except Exception as exc:
        leaderboard_error = f"Leaderboard unavailable: {exc}"
    all_runs_df = hd.arrow_frame(
        hd.run_rows(
            _runs,
            records=records,
            source=leaderboard.source.type if leaderboard is not None else None,
        ),
        hd.RUN_COLUMNS,
    )
    runs_df = all_runs_df[all_runs_df["record"].astype("string").fillna("").ne("")]
    records_df = hd.arrow_frame(hd.record_rows(records), hd.RECORD_COLUMNS)
    return records, leaderboard, leaderboard_error, records_df, runs_df, all_runs_df


@app.cell
def leaderboard_filter_state():
    get_filter_revision, _set_filter_revision = mo.state(0, allow_self_loops=True)
    run_filters = hc.LeaderboardFilters(
        on_change=lambda: _set_filter_revision(lambda revision: revision + 1)
    )
    return get_filter_revision, run_filters


@app.cell
def selection_state(
    all_runs_df,
    leaderboard,
    records_df,
    requested_record,
    run_filters,
):
    metrics = hd.metric_names(all_runs_df)
    if leaderboard is not None and leaderboard.metrics is not None:
        metrics = [item.name for item in leaderboard.metrics]
    tree_metric = metrics[0] if metrics else None
    _run_order = "Run order"
    while _run_order in metrics:
        _run_order += " (execution)"
    x_axis = mo.ui.dropdown(
        {_run_order: None, **{name: name for name in metrics}},
        value=_run_order,
        label="X axis",
    )
    y_axis = mo.ui.dropdown(metrics, value=tree_metric, label="Y axis")
    _initial_key = (
        f"record:{requested_record}"
        if requested_record in set(records_df["record"])
        else None
    )
    get_selection, _set_selection = mo.state(_initial_key)

    def select_key(key):
        if key is None:
            run_filters.clear_record_filters()
        _set_selection(key)

    return (
        get_selection,
        metrics,
        select_key,
        tree_metric,
        x_axis,
        y_axis,
    )


@app.cell
def selected_context(all_runs_df, get_selection, records):
    _key = get_selection() or ""
    _kind, _, _identifier = _key.partition(":")
    selected_run = hd.find_run(all_runs_df, _identifier) if _kind == "run" else None
    _record_id = (
        selected_run["record"]
        if selected_run is not None
        else (_identifier if _kind == "record" else None)
    )
    selected_record = next(
        (record for record in records if record.id == _record_id), None
    )
    return selected_record, selected_run


@app.cell
def navigation_setup():
    get_navigation_revision, _set_navigation_revision = mo.state(
        0, allow_self_loops=True
    )
    navigation = hc.RecordNavigation(
        on_change=lambda: _set_navigation_revision(lambda revision: revision + 1)
    )
    return get_navigation_revision, navigation


@app.cell
def record_navigation(
    records, select_key, selected_record, get_navigation_revision, navigation
):
    get_navigation_revision()
    navigation.render(
        records,
        selected_id=selected_record.id if selected_record else None,
        on_select=lambda record_id: select_key(f"record:{record_id}"),
    )
    return


@app.cell
def record_editor(project_context):
    get_editor_revision, _set_editor_revision = mo.state(0, allow_self_loops=True)

    def save_markdown(record, markdown):
        record.markdown = markdown
        project_context.vault.save(record, overwrite=True)
        return record

    markdown_editor = hc.MarkdownEditor(
        on_save=save_markdown,
        on_change=lambda: _set_editor_revision(lambda revision: revision + 1),
    )
    return get_editor_revision, markdown_editor


@app.cell
def record_details(
    all_runs_df,
    get_editor_revision,
    markdown_editor,
    repository_url,
    selected_record,
    selected_run,
):
    get_editor_revision()
    _title = "Selected record"
    if selected_record is None and selected_run is not None:
        _title = "Run details"
        _details = hc.run_details(selected_run)
    else:
        _runs = (
            all_runs_df.loc[all_runs_df["record"] == selected_record.id].to_dict(
                "records"
            )
            if selected_record is not None
            else []
        )
        _details = hc.markdown_view(
            selected_record,
            editor=markdown_editor,
            repository_url=repository_url,
            runs=_runs,
            open_run_id=selected_run["run"] if selected_run is not None else None,
        )
    mo.vstack([mo.Html(f'<h2 class="record-details-heading">{_title}</h2>'), _details])
    return


@app.cell
def leaderboard_filters(
    all_runs_df, get_filter_revision, records, run_filters, selected_record
):
    get_filter_revision()
    _record_id = selected_record.id if selected_record else None
    leaderboard_runs, plot_scope = run_filters.apply(all_runs_df, records, _record_id)
    run_filters.render(_record_id)
    return leaderboard_runs, plot_scope


@app.cell
def leaderboard_plot(
    leaderboard,
    leaderboard_runs,
    plot_scope,
    select_key,
    selected_record,
    selected_run,
    x_axis,
    y_axis,
):
    def _select_run(run_id):
        select_key(f"run:{run_id}")

    _x, _y = x_axis.value, y_axis.value
    _record_id = selected_record.id if selected_record else None
    _run_id = selected_run["run"] if selected_run is not None else None
    if _x is None:
        plot_view = hc.performance_plot(
            leaderboard_runs,
            scope=plot_scope,
            metric=_y,
            selected_record_id=_record_id,
            selected_run_id=_run_id,
            on_run_select=_select_run,
        )
    else:
        _configured_metrics = leaderboard.metrics if leaderboard is not None else None
        _directions = {item.name: item.direction for item in _configured_metrics or ()}
        plot_view = hc.tradeoff_plot(
            leaderboard_runs,
            x_metric=_x,
            y_metric=_y,
            x_objective=_directions.get(_x) or "minimize",
            y_objective=_directions.get(_y) or "minimize",
            selected_record_id=_record_id,
            selected_run_id=_run_id,
            on_run_select=_select_run,
        )
    plot_view = mo.vstack(
        [plot_view, mo.hstack([x_axis, y_axis], justify="start", wrap=True)]
    )
    return (plot_view,)


@app.cell
def leaderboard_table_controls():
    include_records = mo.ui.checkbox(
        label='<span class="record-filter-chip show-records-chip">Show records</span>',
        value=False,
    )
    return (include_records,)


@app.cell
def leaderboard_table(
    get_selection,
    include_records,
    leaderboard_runs,
    records_df,
    select_key,
    tree_metric,
):
    tree = hc.run_table(
        records_df,
        leaderboard_runs,
        tree_metric,
        selected_key=get_selection(),
        on_select=select_key,
        include_records=include_records.value,
    )
    table_view = mo.vstack([include_records, tree])
    return tree, table_view


@app.cell
def leaderboard_header(leaderboard_selector):
    leaderboard_mode = mo.ui.tabs({"Plot": "", "Table": ""}, value="Plot")
    mo.Html(
        '<div class="dashboard-block-header"><h2>Leaderboard</h2>'
        f"{leaderboard_selector.text}"
        f'<div class="dashboard-block-mode">{leaderboard_mode.text}</div></div>'
    )
    return (leaderboard_mode,)


@app.cell
def leaderboard_view(leaderboard_error, leaderboard_mode, plot_view, table_view):
    _view = plot_view if leaderboard_mode.value == "Plot" else table_view
    _messages = (
        [mo.callout(mo.md(leaderboard_error), kind="warn")] if leaderboard_error else []
    )
    mo.vstack([*_messages, _view])
    return


if __name__ == "__main__":
    app.run()
