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
        label="Leaderboard",
    )
    include_unattributed = mo.ui.checkbox(
        label="Include unattributed runs", value=False
    )
    mo.hstack([leaderboard_selector, include_unattributed, refresh], justify="end")
    return refresh, leaderboard_selector, include_unattributed


@app.cell
def project_data(project_context, refresh, leaderboard_selector, include_unattributed):
    _refreshes = refresh.value
    records = project_context.vault.list()
    leaderboard = None
    leaderboard_error = None
    _runs = []
    try:
        leaderboard = Leaderboard.load(project_context, leaderboard_selector.value)
        _runs = leaderboard.list_runs(
            project_context, include_unattributed=include_unattributed.value
        )
    except Exception as exc:
        leaderboard_error = f"Leaderboard unavailable: {exc}"
    runs_df = hd.arrow_frame(
        hd.run_rows(
            _runs,
            records=records,
            source=leaderboard.source.type if leaderboard is not None else None,
        ),
        hd.RUN_COLUMNS,
    )
    records_df = hd.arrow_frame(hd.record_rows(records), hd.RECORD_COLUMNS)
    return records, leaderboard, leaderboard_error, records_df, runs_df


@app.cell
def selection_state(leaderboard, records_df, requested_record, runs_df):
    paths = dict(zip(records_df["record"], records_df["record_path"], strict=True))
    metrics = hd.metric_names(runs_df)
    if leaderboard is not None and leaderboard.metrics is not None:
        metrics = [item.name for item in leaderboard.metrics]
    tree_metric = metrics[0] if metrics else None
    initial_record = requested_record if requested_record in paths else None
    get_selection, _set_selection = mo.state(
        (f"record:{initial_record}" if initial_record else None, initial_record)
    )

    def select_key(key):
        _set_selection(
            lambda previous: (
                key,
                hd.selection_scope(key, previous[1], paths, runs_df),
            )
        )

    return get_selection, initial_record, metrics, paths, select_key, tree_metric


@app.cell
def run_table(
    get_selection, leaderboard_error, records_df, runs_df, select_key, tree_metric
):
    tree = hc.run_table(
        records_df,
        runs_df,
        tree_metric,
        selected_key=get_selection()[0],
        on_select=select_key,
    )
    _messages = (
        [mo.callout(mo.md(leaderboard_error), kind="warn")] if leaderboard_error else []
    )
    if records_df.empty:
        _messages.append(
            mo.md("No records yet. Create one with `hiraku record create`.")
        )
    mo.vstack([*_messages, tree])
    return (tree,)


@app.cell
def selected_context(get_selection, records, runs_df):
    _key, scope = get_selection()
    _key = _key or ""
    _kind, _, _identifier = _key.partition(":")
    selected_run = hd.find_run(runs_df, _identifier) if _kind == "run" else None
    _record_id = selected_run["record"] if selected_run is not None else scope
    selected_record = next(
        (record for record in records if record.id == _record_id), None
    )
    return scope, selected_record, selected_run


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
    get_editor_revision, markdown_editor, selected_record, repository_url, runs_df
):
    get_editor_revision()
    hc.markdown_view(
        selected_record,
        editor=markdown_editor,
        repository_url=repository_url,
        runs=[
            row
            for row in runs_df.to_dict("records")
            if selected_record is not None and row["record"] == selected_record.id
        ],
    )
    return


@app.cell
def run_details(selected_run):
    hc.run_details(selected_run)
    return


@app.cell
def performance_controls(metrics, tree_metric):
    metric = hc.performance_controls(metrics, tree_metric)
    return (metric,)


@app.cell
def performance_plot(metric, paths, runs_df, scope, selected_run):
    _content = hc.performance_plot(
        hd.subtree(runs_df, paths.get(scope)),
        scope=scope,
        metric=metric.value,
        selected_run=selected_run,
    )
    mo.vstack([metric, _content])
    return


@app.cell
def tradeoff_controls(metrics, leaderboard):
    _directions = (
        {item.name: item.direction for item in (leaderboard.metrics or ())}
        if leaderboard is not None
        else {}
    )
    x_metric, x_objective, y_metric, y_objective = hc.tradeoff_controls(
        _directions, metrics
    )
    return x_metric, x_objective, y_metric, y_objective


@app.cell
def tradeoff_plot(paths, runs_df, scope, x_metric, x_objective, y_metric, y_objective):
    _content = hc.tradeoff_plot(
        hd.subtree(runs_df, paths.get(scope)),
        x_metric=x_metric.value,
        y_metric=y_metric.value,
        x_objective=x_objective.value,
        y_objective=y_objective.value,
    )
    mo.vstack(
        [mo.hstack([x_metric, x_objective, y_metric, y_objective], wrap=True), _content]
    )
    return


if __name__ == "__main__":
    app.run()
