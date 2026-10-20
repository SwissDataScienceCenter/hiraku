# /// script
# [tool.marimo.display]
# theme = "system"
#
# [tool.marimo-studio]
# default = "dashboard"
# view_root = "studio"
# ///
"""Dashboard wiring. Start with ``hiraku dashboard`` or edit a project copy.

Reusable UI lives in ``hiraku.dashboard.components``; the Studio view owns
branding and layout. Keep loading, selection, and reactive widget reads here.
"""

import marimo

__generated_with = "0.25.1"
app = marimo.App()


with app.setup:
    import os
    from pathlib import Path

    import marimo as mo

    from hiraku.config import HirakuConfig
    from hiraku.dashboard import components as hc
    from hiraku.dashboard import data as hd


@app.cell
def data_setup():
    _root = os.environ.get("HIRAKU_PROJECT_ROOT")
    # Project copies opened directly in marimo discover their own project.
    config = (
        HirakuConfig.load(Path(_root) / "hiraku.yml")
        if _root
        else HirakuConfig.discover(mo.notebook_dir())
    )
    requested_record = os.environ.get("HIRAKU_DASHBOARD_RECORD") or None
    return config, requested_record


@app.cell
def project_name(config):
    mo.plain_text(config.root.name)
    return


@app.cell
def refresh_control():
    refresh = mo.ui.button(label="Refresh", value=0, on_click=lambda count: count + 1)
    mo.hstack([refresh], justify="end")
    return (refresh,)


@app.cell
def project_data(config, refresh):
    _refreshes = refresh.value
    project = hd.load(config)
    runs_df = hd.arrow_frame(hd.run_rows(project), hd.RUN_COLUMNS)
    records_df = hd.arrow_frame(hd.record_rows(project.records), hd.RECORD_COLUMNS)
    return project, records_df, runs_df


@app.cell
def selection_state(config, records_df, requested_record, runs_df):
    paths = dict(zip(records_df["record"], records_df["record_path"], strict=True))
    metrics = hd.metric_names(runs_df)
    tree_metric = hd.default_metric(records_df, metrics)
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
def run_table(initial_record, project, records_df, runs_df, tree_metric):
    tree = hc.run_table(records_df, runs_df, tree_metric, initial_record)
    _messages = [mo.callout(mo.md(error), kind="warn") for error in project.errors]
    if records_df.empty:
        _messages.append(
            mo.md("No records yet. Create one with `hiraku record create`.")
        )
    mo.vstack([*_messages, tree])
    return (tree,)


@app.cell
def selected_context(get_scope, paths, project, runs_df, set_scope, tree):
    _key = tree.value[0]["key"] if tree.value else f"record:{get_scope()}"
    _kind, _, _identifier = _key.partition(":")
    selected_run = hd.find_run(runs_df, _identifier) if _kind == "run" else None
    scope = _identifier if _kind == "record" else get_scope()
    # Keep comparing against the chosen record unless the run lies outside it.
    if selected_run is not None and (
        scope is None or not hd.within(selected_run["record_path"], paths[scope])
    ):
        scope = selected_run["record"]
    set_scope(scope)
    _record_id = selected_run["record"] if selected_run is not None else scope
    selected_record = next(
        (record for record in project.records if record.id == _record_id), None
    )
    return scope, selected_record, selected_run


@app.cell
def record_details(selected_record):
    hc.markdown_view(selected_record)
    return


@app.cell
def run_details(selected_run):
    hc.run_details(selected_run)
    return


@app.cell
def metric_controls(metrics, records_df, tree_metric):
    plot_mode, metric, x_metric, x_objective, y_metric, y_objective = (
        hc.metric_controls(records_df, metrics, tree_metric)
    )
    return plot_mode, metric, x_metric, x_objective, y_metric, y_objective


@app.cell
def metric_analysis(
    metric,
    paths,
    runs_df,
    scope,
    selected_run,
    plot_mode,
    x_metric,
    x_objective,
    y_metric,
    y_objective,
):
    _controls = (
        [plot_mode, metric]
        if plot_mode.value == "Performance over runs"
        else [plot_mode, x_metric, x_objective, y_metric, y_objective]
    )
    _content = hc.metric_analysis(
        hd.subtree(runs_df, paths.get(scope)),
        scope=scope,
        mode=plot_mode.value,
        metric=metric.value,
        x_metric=x_metric.value,
        y_metric=y_metric.value,
        x_objective=x_objective.value,
        y_objective=y_objective.value,
        selected_run=selected_run,
    )
    mo.vstack([*_controls, _content])
    return


if __name__ == "__main__":
    app.run()
