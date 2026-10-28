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
    # Studio opens records and files from the agent chat with ?record= and
    # ?artifact= links.
    _params = mo.query_params()
    link = {
        name: (value[0] if isinstance(value, list) else value) or None
        for name in ("record", "artifact")
        for value in [_params.get(name)]
    }
    return config, link


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
    file_entries = hd.file_rows(project)
    files_df = hd.arrow_frame(file_entries, hd.FILE_COLUMNS)
    artifact_entries = hd.artifact_rows(project, config)
    artifacts_df = hd.arrow_frame(artifact_entries, hd.ARTIFACT_COLUMNS)
    return (
        artifact_entries,
        artifacts_df,
        file_entries,
        files_df,
        project,
        records_df,
        runs_df,
    )


@app.cell
def link_target(artifact_entries, link):
    # A linked file selects the dataset that registered it; a link wins over
    # the record the dashboard was started on.
    linked_artifact = link["artifact"]
    linked_file = next(
        (
            row
            for row in artifact_entries
            if linked_artifact
            and row["key"] == f"artifact:{row['dataset']}:{linked_artifact}"
        ),
        None,
    )
    linked_record = link["record"] or (linked_file or {}).get("dataset")
    requested_record = (
        linked_record or os.environ.get("HIRAKU_DASHBOARD_RECORD") or None
    )
    return linked_artifact, linked_file, linked_record, requested_record


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
    # The run table refolds on its own selection, so the cell that owns the
    # table must rerun when it sets this state.
    get_selection, _set_selection = mo.state(
        (f"record:{initial_record}" if initial_record else None, initial_record),
        allow_self_loops=True,
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
def linked_scope():
    # A linked record is shown on its own branch until the reader widens it.
    get_show_all, _set_show_all = mo.state(False)
    show_all = mo.ui.button(
        label="Show all", on_click=lambda _: _set_show_all(True)
    )
    return get_show_all, show_all


@app.cell
def run_table(
    config,
    get_selection,
    get_show_all,
    initial_record,
    linked_file,
    linked_record,
    paths,
    project,
    records_df,
    requested_record,
    runs_df,
    select_key,
    show_all,
    tree_metric,
):
    _key = get_selection()[0]
    # The first record is selected only as a fallback; it should not unfold.
    _fallback = initial_record not in (requested_record, config.active_record_id)
    _linked_path = paths.get(linked_record) if linked_record else None
    _narrow = _linked_path is not None and not get_show_all()
    _records, _runs = (
        hd.linked_view(records_df, runs_df, _linked_path)
        if _narrow
        else (records_df, runs_df)
    )
    tree = hc.run_table(
        _records,
        _runs,
        tree_metric,
        selected_key=_key,
        on_select=select_key,
        unfold_selected=not (_fallback and _key == f"record:{initial_record}"),
    )
    _messages = [mo.callout(mo.md(error), kind="warn") for error in project.errors]
    if _narrow:
        _label = _records.loc[_records["record"] == linked_record, "label"].iloc[0]
        _file = (
            f" Its file `{linked_file['path']}` is selected under Observed files."
            if linked_file
            else ""
        )
        _messages.append(
            mo.hstack(
                [mo.md(f"Showing **{_label}** (`{linked_record}`), opened from a "
                       f"link, with its parent records and runs.{_file}"), show_all],
                justify="space-between",
                align="center",
            )
        )
    if records_df.empty:
        _messages.append(
            mo.md("No records yet. Create one with `hiraku record create`.")
        )
    mo.vstack([*_messages, tree])
    return (tree,)


@app.cell
def selected_context(get_selection, project, runs_df):
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
def file_table(file_entries):
    file_selector = hc.file_table(file_entries)
    mo.vstack([file_selector])
    return (file_selector,)


@app.cell
def file_details(file_entries, file_selector, select_key):
    _details, file_buttons = hc.file_details(
        file_entries, file_selector.value, on_select=select_key
    )
    mo.vstack([_details])
    return (file_buttons,)


@app.cell
def artifact_table(artifact_entries, get_show_all, linked_artifact, linked_record):
    # A linked file is shown among its dataset's files, already selected.
    _narrow = bool(linked_artifact and linked_record) and not get_show_all()
    _entries = (
        [row for row in artifact_entries if row["dataset"] == linked_record]
        if _narrow
        else artifact_entries
    )
    artifact_selector = hc.artifact_table(
        _entries,
        selected_key=(
            f"artifact:{linked_record}:{linked_artifact}" if linked_artifact else None
        ),
    )
    _count = sum(row["key"].startswith("artifact:") for row in _entries)
    mo.vstack([mo.md(f"### Observed files ({_count})"), artifact_selector])
    return (artifact_selector,)


@app.cell
def artifact_details(artifact_entries, artifact_selector, select_key):
    _details, artifact_buttons = hc.artifact_details(
        artifact_entries, artifact_selector.value, on_select=select_key
    )
    mo.vstack([_details])
    return (artifact_buttons,)


@app.cell
def documentation_table(artifact_entries):
    documentation_selector = hc.artifact_table(artifact_entries, documentation=True)
    mo.vstack([
        mo.md("### SOP references\nFilenames mentioned in SOPs without a matching "
              "observed file. Applicability to a sample has not been established."),
        documentation_selector,
    ])
    return (documentation_selector,)


@app.cell
def documentation_details(artifact_entries, documentation_selector, select_key):
    _details, documentation_buttons = hc.artifact_details(
        artifact_entries, documentation_selector.value, on_select=select_key
    )
    mo.vstack([_details])
    return (documentation_buttons,)


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
