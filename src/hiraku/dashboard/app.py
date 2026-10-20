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
app = marimo.App(css_file="studio/dashboard/components.css")


with app.setup:
    import os
    from pathlib import Path

    import marimo as mo

    from hiraku.base import BaseRecord
    from hiraku.config import HirakuConfig
    from hiraku.context import list_contexts
    from hiraku.dashboard import components as hc
    from hiraku.dashboard import data as hd
    from hiraku.errors import HirakuError


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
def context_data(config, refresh):
    _refreshes = refresh.value
    context_error = ""
    try:
        contexts = list_contexts(config=config)
    except (HirakuError, OSError, ValueError) as _exc:
        contexts = []
        context_error = f"Contexts unavailable: {_exc}"
    return context_error, contexts


@app.cell
def context_list(context_error, contexts):
    context_selector = hc.record_selector(contexts, label="Context")
    _messages = []
    if context_error:
        _messages.append(mo.callout(mo.plain_text(context_error), kind="warn"))
    elif not contexts:
        _messages.append(
            mo.md("No contexts yet. Create one with `hiraku context create`.")
        )
    mo.vstack([*_messages, context_selector])
    return (context_selector,)


@app.cell
def context_details(context_selector, contexts):
    selected_context = next(
        (context for context in contexts if context.id == context_selector.value), None
    )
    _content = (
        mo.md("No context selected.")
        if selected_context is None
        else hc.markdown_view(selected_context)
    )
    mo.vstack([_content])
    return (selected_context,)


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
def run_table(get_selection, project, records_df, runs_df, select_key, tree_metric):
    tree = hc.run_table(
        records_df,
        runs_df,
        tree_metric,
        selected_key=get_selection()[0],
        on_select=select_key,
    )
    _messages = [mo.callout(mo.md(error), kind="warn") for error in project.errors]
    if records_df.empty:
        _messages.append(
            mo.md("No records yet. Create one with `hiraku record create`.")
        )
    mo.vstack([*_messages, tree])
    return (tree,)


@app.cell
def record_selection(get_selection, project, runs_df):
    _key, scope = get_selection()
    _key = _key or ""
    _kind, _, _identifier = _key.partition(":")
    selected_run = hd.find_run(runs_df, _identifier) if _kind == "run" else None
    _record_id = selected_run["record"] if selected_run is not None else scope
    selected_record = next(
        (record for record in project.records if record.id == _record_id), None
    )
    return scope, selected_record, selected_run


@app.cell
def record_navigation(project, select_key, selected_record):
    _navigation, record_buttons = hc.record_tree(
        project.records,
        selected_record.id if selected_record else None,
        on_select=lambda record_id: select_key(f"record:{record_id}"),
    )
    mo.vstack([_navigation])
    return (record_buttons,)


@app.cell
def record_editor():
    get_editor_revision, _set_editor_revision = mo.state(0, allow_self_loops=True)
    markdown_editor = hc.MarkdownEditor(
        on_save=BaseRecord.save_markdown,
        on_change=lambda: _set_editor_revision(lambda revision: revision + 1),
    )
    return get_editor_revision, markdown_editor


@app.cell
def record_details(get_editor_revision, markdown_editor, selected_record):
    get_editor_revision()
    hc.markdown_view(selected_record, editor=markdown_editor)
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
def tradeoff_controls(metrics, records_df):
    x_metric, x_objective, y_metric, y_objective = hc.tradeoff_controls(
        records_df, metrics
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
