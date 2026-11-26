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
    _requested_record = os.environ.get("HIRAKU_DASHBOARD_RECORD")
    _requested_leaderboard = os.environ.get("HIRAKU_DASHBOARD_LEADERBOARD")
    repository_url = hd.github_repository(project_context)
    leaderboard_names = Leaderboard.list(project_context)
    _source = (
        _requested_leaderboard
        if _requested_leaderboard in leaderboard_names
        else (leaderboard_names[0] if len(leaderboard_names) == 1 else None)
    )
    _key = f"record:{_requested_record}" if _requested_record else None
    # Marimo invalidates readers of each state value independently.
    get_source, set_source = mo.state(_source, allow_self_loops=True)
    get_selection, _set_selection = mo.state(_key, allow_self_loops=True)
    get_run_filters, _set_run_filters = mo.state(frozenset(), allow_self_loops=True)
    get_record_filters, _set_record_filters = mo.state(
        frozenset(), allow_self_loops=True
    )
    get_leaderboard_mode, set_leaderboard_mode = mo.state("Plot", allow_self_loops=True)
    get_record_mode, set_record_mode = mo.state("Files", allow_self_loops=True)
    get_axes, set_axes = mo.state((None, None), allow_self_loops=True)
    get_show_records, set_show_records = mo.state(False, allow_self_loops=True)

    def select_key(key):
        if key is None:
            _set_run_filters(lambda filters: filters - {"record"})
        _set_selection(key)

    def select_record(record_id):
        select_key(f"record:{record_id}")

    def select_run(run_id):
        select_key(f"run:{run_id}")

    def toggle_run_filter(name):
        _set_run_filters(lambda filters: filters ^ {name})

    def toggle_record_filter(field, value):
        _set_record_filters(lambda filters: filters ^ {(field, value)})

    return (
        project_context,
        repository_url,
        leaderboard_names,
        get_source,
        set_source,
        get_selection,
        select_key,
        select_record,
        select_run,
        get_run_filters,
        toggle_run_filter,
        get_record_filters,
        toggle_record_filter,
        get_leaderboard_mode,
        set_leaderboard_mode,
        get_record_mode,
        set_record_mode,
        get_axes,
        set_axes,
        get_show_records,
        set_show_records,
    )


@app.cell
def refresh_control():
    refresh = mo.ui.button(label="Refresh", value=0, on_click=lambda count: count + 1)
    mo.hstack([refresh], justify="end")
    return (refresh,)


@app.cell
def project_data(project_context, refresh, get_source):
    _refreshes = refresh.value
    records = project_context.vault.list()
    leaderboard = None
    leaderboard_error = None
    _runs = []
    try:
        leaderboard = Leaderboard.load(project_context, get_source())
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
    metrics = (
        [item.name for item in leaderboard.metrics]
        if leaderboard is not None and leaderboard.metrics is not None
        else hd.metric_names(all_runs_df)
    )
    return (
        records,
        leaderboard,
        leaderboard_error,
        records_df,
        runs_df,
        all_runs_df,
        metrics,
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
def record_header(
    records, get_record_filters, get_record_mode, set_record_mode, toggle_record_filter
):
    record_mode = mo.ui.tabs(
        {"Files": "", "Lineage": ""}, value=get_record_mode(), on_change=set_record_mode
    )
    record_types = sorted(
        value for field, value in get_record_filters() if field == "type"
    )
    record_states = sorted(
        value for field, value in get_record_filters() if field == "state"
    )
    _filters, record_filter_buttons = hc.record_filters(
        records, toggle_record_filter, types=record_types, states=record_states
    )
    _heading = mo.Html(
        '<div class="record-navigation-header"><h2>Records</h2>'
        f'<div class="record-navigation-mode">{record_mode.text}</div></div>'
    )
    mo.vstack([_heading, _filters])
    return record_mode, record_types, record_states, record_filter_buttons


@app.cell
def record_files(
    get_record_mode,
    record_types,
    record_states,
    records,
    select_record,
    selected_record,
):
    _view, file_buttons = hc.filesystem_tree(
        records,
        selected_record.id if selected_record else None,
        select_record,
        types=record_types,
        states=record_states,
    )
    mo.vstack([_view] if get_record_mode() == "Files" else [])
    return (file_buttons,)


@app.cell
def record_tree(
    get_record_mode,
    record_types,
    record_states,
    records,
    select_record,
    selected_record,
):
    _view, lineage_buttons = hc.lineage_tree(
        records,
        selected_record.id if selected_record else None,
        select_record,
        types=record_types,
        states=record_states,
    )
    mo.vstack([_view] if get_record_mode() == "Lineage" else [])
    return (lineage_buttons,)


@app.cell
def record_details(
    all_runs_df,
    repository_url,
    selected_record,
    selected_run,
):
    _title = "Record"
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
            repository_url=repository_url,
            runs=_runs,
            open_run_id=selected_run["run"] if selected_run is not None else None,
        )
    mo.vstack([mo.Html(f'<h2 class="record-details-heading">{_title}</h2>'), _details])
    return


@app.cell
def filtered_runs(all_runs_df, get_run_filters, records, selected_record):
    run_filters = get_run_filters()
    _record_id = (
        selected_record.id if selected_record and "record" in run_filters else None
    )
    leaderboard_runs = hd.filter_leaderboard_runs(
        all_runs_df,
        records,
        _record_id,
        include_unattributed="unattributed" in run_filters,
    )
    return run_filters, leaderboard_runs


@app.cell
def leaderboard_header(
    get_leaderboard_mode,
    get_source,
    leaderboard_error,
    leaderboard_names,
    leaderboard_runs,
    run_filters,
    selected_record,
    set_leaderboard_mode,
    set_source,
    toggle_run_filter,
):
    leaderboard_selector = mo.ui.dropdown(
        leaderboard_names,
        value=get_source(),
        label='<span class="leaderboard-selector-label">Leaderboard</span>',
        on_change=set_source,
    )
    leaderboard_mode = mo.ui.tabs(
        {"Plot": "", "Table": ""},
        value=get_leaderboard_mode(),
        on_change=set_leaderboard_mode,
    )
    _heading = mo.Html(
        '<div class="dashboard-block-header"><h2>Leaderboard</h2>'
        f"{leaderboard_selector.text}"
        f'<div class="dashboard-block-mode">{leaderboard_mode.text}</div></div>'
    )
    _filters, run_filter_buttons = hc.leaderboard_filters(
        run_filters,
        selected_record is not None,
        len(leaderboard_runs),
        toggle_run_filter,
    )
    _content = [_heading, _filters]
    if leaderboard_error:
        _content.append(mo.callout(mo.md(leaderboard_error), kind="warn"))
    mo.vstack(_content, gap=0)
    return leaderboard_mode, leaderboard_selector, run_filter_buttons


@app.cell
def leaderboard_plot(
    leaderboard,
    leaderboard_runs,
    get_axes,
    get_leaderboard_mode,
    metrics,
    select_run,
    selected_record,
    selected_run,
    set_axes,
):
    _x, _y = get_axes()
    _x = _x if _x in metrics else None
    _y = _y if _y in metrics else next(iter(metrics), None)
    x_axis, y_axis = hc.plot_axes(metrics, _x, _y, set_axes)
    _record_id = selected_record.id if selected_record else None
    _run_id = selected_run["run"] if selected_run is not None else None
    _configured_metrics = leaderboard.metrics if leaderboard is not None else None
    plot_view = hc.performance_plot(
        leaderboard_runs,
        x_metric=_x,
        y_metric=_y,
        directions={item.name: item.direction for item in _configured_metrics or ()},
        selected_record_id=_record_id,
        selected_run_id=_run_id,
        on_run_select=select_run,
    )
    plot_view = mo.vstack(
        [plot_view, mo.hstack([x_axis, y_axis], justify="start", wrap=True)]
    )
    mo.vstack([plot_view] if get_leaderboard_mode() == "Plot" else [])
    return plot_view, x_axis, y_axis


@app.cell
def leaderboard_table(
    get_leaderboard_mode,
    get_selection,
    get_show_records,
    leaderboard_runs,
    metrics,
    records_df,
    select_key,
    set_show_records,
):
    _show_records = get_show_records()
    include_records = mo.ui.checkbox(
        label='<span class="record-filter-chip show-records-chip">Show records</span>',
        value=_show_records,
        on_change=set_show_records,
    )
    tree = hc.run_table(
        records_df,
        leaderboard_runs,
        next(iter(metrics), None),
        selected_key=get_selection(),
        on_select=select_key,
        include_records=_show_records,
    )
    table_view = mo.vstack([include_records, tree])
    mo.vstack([table_view] if get_leaderboard_mode() == "Table" else [])
    return tree, table_view, include_records


if __name__ == "__main__":
    app.run()
