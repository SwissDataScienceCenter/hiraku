"""Read-only browser using Hiraku's Python API.

From the project to browse, run `hiraku dashboard` (or `--edit` to edit a
project copy); it starts marimo in Hiraku's environment.

Use any initialized Hiraku project containing records and tracked runs.
Reload to read updated records. Loading and rendering live in
`hiraku.dashboard.components`. To add a plot, add a cell that reads `runs`,
`record`, or `selected_id` and place it in views/dashboard/index.html with
`<marimo-cell name="...">`.
"""

import marimo

__generated_with = "0.25.1"
app = marimo.App()

with app.setup:
    import marimo as mo

    from hiraku.dashboard import components as hd


@app.cell
def project_data():
    # Project config and records; read once per page load.
    config, records, initial_id, project_error = hd.load_project()
    record_index = {item.id: item for item in records}
    return config, initial_id, project_error, record_index, records


@app.cell
def selection_state(initial_id, record_index):
    # Selected record and metric, shared by all views.
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
    # Current record; downstream cells rerun when it changes.
    selected_id, selection_message = get_selection()
    record = record_index.get(selected_id)
    return record, selected_id, selection_message


@app.cell
def record_tree(project_error, records, select_record, selected_id):
    # Navigation tree; clicking a record selects it.
    tree, tree_buttons = hd.record_tree(records, selected_id, select_record)
    mo.vstack(
        [
            mo.md("## Records"),
            tree if records else mo.md(project_error or "No records found."),
        ]
    )
    return (tree_buttons,)


@app.cell
def record_details(project_error, record):
    # Front matter and Markdown of the selected record.
    if record is None:
        mo.output.replace(mo.md(project_error or "No records found."))
    else:
        mo.output.replace(hd.record_details(record))
    return


@app.cell
def tracking_data(config, record):
    # Metric declarations and tracked runs of the selected record.
    declarations, runs, tracking_error = hd.load_runs(record, config)
    return declarations, runs, tracking_error


@app.cell
def metric_control(declarations, get_metric, runs, set_metric):
    # Metric dropdown; the choice persists across records.
    metric_names = hd.metric_names(declarations, runs)
    preferred = get_metric()
    metric_selector = mo.ui.dropdown(
        options=metric_names,
        value=preferred
        if preferred in metric_names
        else next(iter(metric_names), None),
        label="Metric",
        on_change=set_metric,
    )
    metric_selector if metric_names else mo.md("No numeric metrics available.")
    return (metric_selector,)


@app.cell
def run_plot(
    declarations,
    metric_selector,
    runs,
    select_record,
    selected_id,
    selection_message,
    tracking_error,
):
    # Run chart; replace this cell to change the plot.
    metric = metric_selector.value
    direction = hd.metric_direction(declarations, metric)
    plot_data = hd.run_frame(runs, metric, selected_id, direction)
    run_chart = (
        None if plot_data.empty else hd.run_chart(plot_data, metric, select_record)
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
    return direction, plot_data, run_chart


if __name__ == "__main__":
    app.run()
