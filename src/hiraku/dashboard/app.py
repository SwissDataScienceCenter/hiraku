# /// script
# [tool.marimo.display]
# theme = "system"
# ///
"""Hiraku dashboard with a research tree for the ``research`` workflow.

The bundled dashboard (Hiraku #116, commit 87cbe7a) plus a "Research tree"
section: the experiment tree of a task, each experiment coloured by the step it
needs, the session's current step, and a card with the selected record.
Copy it to ``hiraku/dashboard/app.py`` and run ``hiraku dashboard``.
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
def _():
    import pandas as pd

    from hiraku.record import list_records
    from hiraku.workflows.session import list_sessions
    from hiraku.workflows.workflow import load_workflow

    # One colour per state of an experiment, in legend order. The step comes
    # from the research workflow; finished experiments show their verdict.
    STATES = {
        "task": "#5461a6",
        "to implement": "#848484",
        "to launch": "#3b82f6",
        "running": "#eab308",
        "to analyze": "#8b5cf6",
        "to triage": "#06b6d4",
        "to design follow-ups": "#90ca42",
        "in review": "#f472b6",
        "supports": "#10b981",
        "refutes": "#ef4444",
        "inconclusive": "#a3a3a3",
    }
    STEP_STATES = {
        "implement": "to implement",
        "launch": "to launch",
        "waiting": "running",
        "analyze": "to analyze",
        "triage": "to triage",
        "design": "to design follow-ups",
        "review_design": "in review",
        "review_code": "in review",
        "review_result": "in review",
    }

    def experiment_state(item):
        if item["step"] == "done":
            return item["verdict"] or "inconclusive"
        return STEP_STATES.get(item["step"], item["step"])

    def tree_layout(overview):
        """Place the task at the top and each experiment below its parent.

        Leaves get consecutive x positions in tree order; a parent sits above
        the middle of its children.
        """
        task = overview["task"]["record_id"]
        children = {}
        for item in overview["experiments"]:
            children.setdefault(item["parent"], []).append(item["record_id"])
        x, depth, leaves = {}, {task: 0}, [0]

        def place(record_id):
            kids = children.get(record_id, [])
            for kid in kids:
                depth[kid] = depth[record_id] + 1
                place(kid)
            if kids:
                x[record_id] = (x[kids[0]] + x[kids[-1]]) / 2
            else:
                x[record_id] = leaves[0]
                leaves[0] += 1

        place(task)
        items = {item["record_id"]: item for item in overview["experiments"]}
        nodes = [
            {
                "id": task,
                "x": x[task],
                "y": 0,
                "state": "task",
                "summary": overview["task"]["summary"] or "",
            }
        ]
        edges = []
        for record_id, item in items.items():
            nodes.append(
                {
                    "id": record_id,
                    "x": x[record_id],
                    "y": depth[record_id],
                    "state": experiment_state(item),
                    "summary": item["summary"] or "",
                }
            )
            edges.append(
                {
                    "x": x[item["parent"]],
                    "y": depth[item["parent"]],
                    "x2": x[record_id],
                    "y2": depth[record_id],
                }
            )
        return pd.DataFrame(nodes), pd.DataFrame(edges, columns=["x", "y", "x2", "y2"])

    return (
        STATES,
        experiment_state,
        list_records,
        list_sessions,
        load_workflow,
        pd,
        tree_layout,
    )


@app.cell
def _(config, list_records, list_sessions, load_workflow, refresh):
    _refreshes = refresh.value  # reread records and sessions on Refresh
    try:
        research = load_workflow("research", config=config)
        research_error = None
    except Exception as exc:  # the workflow is optional in a project
        research, research_error = None, str(exc)
    # Active sessions first, newest first.
    research_sessions = sorted(
        (item for item in list_sessions(config=config) if item.workflow == "research"),
        key=lambda item: (item.status != "active", -item.updated_at.timestamp()),
    )
    research_tasks = [
        item.id for item in list_records(config=config) if item.type == "task"
    ]
    return research, research_error, research_sessions, research_tasks


@app.cell
def _(config, mo, research_sessions, research_tasks):
    _from_sessions = [
        getattr(item.state, "task", None)
        for item in research_sessions
        if getattr(item.state, "task", None) in research_tasks
    ]
    _default = next(
        iter(
            [
                *_from_sessions,
                *(
                    [config.active_record_id]
                    if config.active_record_id in research_tasks
                    else []
                ),
                *research_tasks,
            ]
        ),
        None,
    )
    task_choice = mo.ui.dropdown(research_tasks, value=_default, label="Task")
    return (task_choice,)


@app.cell
def _(research, research_sessions, task_choice):
    research_task = task_choice.value
    # The newest session on this task tells which entrypoint its runs use and
    # what the workflow works on now.
    research_session = next(
        (
            item
            for item in research_sessions
            if getattr(item.state, "task", None) == research_task
        ),
        None,
    )
    _entrypoint = (
        getattr(research_session.state, "entrypoint", "train")
        if research_session is not None
        else "train"
    )
    overview = (
        research.overview(research_task, _entrypoint)
        if research is not None and research_task
        else None
    )
    return overview, research_session, research_task


@app.cell
def _(section_heading):
    section_heading("research", "Research tree", open=True)
    return


@app.cell
def _(STATES, alt, mo, overview, research_session, tree_layout):
    research_nodes = None
    research_chart = None
    if overview is not None:
        research_nodes, _edges = tree_layout(overview)
        _current = (
            getattr(research_session.state, "current", None)
            if research_session is not None and research_session.status == "active"
            else None
        )
        _width = max(int(research_nodes["x"].max()) + 1, 2)
        _depth = int(research_nodes["y"].max())
        _x = alt.X("x:Q", axis=None, scale=alt.Scale(domain=[-0.5, _width - 0.5]))
        _y = alt.Y(
            "y:Q",
            axis=None,
            scale=alt.Scale(domain=[-0.4, _depth + 0.6], reverse=True),
        )
        _pick = alt.selection_point(fields=["id"], on="click", name="node")
        _links = (
            alt.Chart(_edges)
            .mark_rule(color="#b8bccf", strokeWidth=2)
            .encode(x=_x, y=_y, x2="x2:Q", y2="y2:Q")
        )
        _dots = (
            alt.Chart(research_nodes)
            .mark_circle(
                size=700, opacity=1, stroke="#ffffff", strokeWidth=2, cursor="pointer"
            )
            .encode(
                x=_x,
                y=_y,
                color=alt.Color(
                    "state:N",
                    scale=alt.Scale(domain=list(STATES), range=list(STATES.values())),
                    legend=alt.Legend(title="State", orient="right"),
                ),
                tooltip=[
                    alt.Tooltip("id:N", title="Record"),
                    alt.Tooltip("state:N", title="State"),
                    alt.Tooltip("summary:N", title="Summary"),
                ],
            )
            .add_params(_pick)
        )
        _ring = (
            alt.Chart(research_nodes)
            .transform_filter(alt.datum.id == (_current or ""))
            .mark_circle(
                size=1500, filled=False, stroke="#f59e0b", strokeWidth=3, opacity=1
            )
            .encode(x=_x, y=_y)
        )
        _labels = (
            alt.Chart(research_nodes)
            .mark_text(align="left", baseline="middle", dx=26, fontSize=12)
            .encode(x=_x, y=_y, text="id:N", color=alt.value("#848484"))
        )
        research_chart = mo.ui.altair_chart(
            alt.layer(_links, _ring, _dots, _labels)
            .properties(width="container", height=140 + 110 * _depth)
            .configure(background="transparent")
            .configure_legend(labelColor="#848484", titleColor="#848484")
            .configure_view(stroke=None),
            legend_selection=False,
        )
    return research_chart, research_nodes


@app.cell
def _(mo, research_nodes, research_session):
    _ids = list(research_nodes["id"]) if research_nodes is not None else []
    # Open on the record the active session works on.
    _current = (
        getattr(research_session.state, "current", None)
        if research_session is not None and research_session.status == "active"
        else None
    )
    research_pick = mo.ui.dropdown(
        _ids, value=_current if _current in _ids else None, label="Or choose a record"
    )
    return (research_pick,)


@app.cell
def _(research_chart, research_pick):
    # A click on the tree wins over the dropdown.
    _clicked = []
    if research_chart is not None:
        _selection = (research_chart.selections or {}).get("node") or {}
        _clicked = _selection.get("id") or []
        if isinstance(_clicked, str):
            _clicked = [_clicked]
    selected_node = _clicked[0] if _clicked else research_pick.value
    return (selected_node,)


@app.cell
def _(
    experiment_state,
    html,
    mo,
    overview,
    research_chart,
    research_error,
    research_pick,
    research_session,
    research_task,
    section_body,
    selected_node,
    task_choice,
):
    def _field(label, value):
        if value in (None, "", []):
            return ""
        return f"<p><strong>{label}</strong> {html.escape(str(value))}</p>"

    def _status():
        if research_session is None:
            return mo.md(
                f"No research session on **{research_task}** yet. Start one in "
                f"Claude Code with `/mcp__hiraku__research task={research_task}`."
            )
        state = research_session.state
        current = getattr(state, "current", None)
        if research_session.status != "active":
            _id, _status = research_session.id, research_session.status
            return mo.md(f"Last session `{_id}` is **{_status}**.")
        doing = f"**{state.node}**" + (f" on **{current}**" if current else "")
        return mo.callout(
            mo.md(
                f"Session `{research_session.id}` is at {doing}. "
                f"Limits: {getattr(state, 'max_experiments', '?')} experiments, "
                f"depth {getattr(state, 'max_depth', '?')}. "
                "The orange ring marks the record it works on."
            ),
            kind="info",
        )

    def _card(record_id):
        if record_id is None:
            return mo.md("Click a node in the tree to see its details.")
        if record_id == overview["task"]["record_id"]:
            task = overview["task"]
            metrics = ", ".join(
                f"{item['name']} ({item['direction']})" for item in task["metrics"]
            )
            return mo.Html(
                f"<h3>{html.escape(record_id)} · task</h3>"
                + _field("Summary", task["summary"])
                + _field("Metrics", metrics)
                + _field("Experiments", len(overview["experiments"]))
            )
        item = next(
            entry
            for entry in overview["experiments"]
            if entry["record_id"] == record_id
        )
        runs = "".join(
            "<li>"
            + html.escape(run["run_id"])
            + ": "
            + html.escape(
                ", ".join(f"{name} = {value}" for name, value in run["metrics"].items())
                or "no metrics yet"
            )
            + "</li>"
            for run in item["runs"]
        )
        triage = (
            f"{item['triage']}: {item['triage_reason'] or ''}"
            if item["triage"]
            else None
        )
        return mo.Html(
            f"<h3>{html.escape(record_id)} · {html.escape(experiment_state(item))}</h3>"
            + _field("Summary", item["summary"])
            + _field("Parent", item["parent"])
            + _field("Hypothesis", item["hypothesis"])
            + _field("Expected", item["expected"])
            + _field("Commit", (item["commit"] or "")[:12])
            + (f"<p><strong>Runs</strong></p><ul>{runs}</ul>" if runs else "")
            + _field("Verdict", item["verdict"])
            + _field("Conclusion", item["conclusion"])
            + _field("Triage", triage)
            + _field("Last failure", item["failure"])
            + "".join(
                _field(
                    f"Review ({review['kind']}, {review['decision']})",
                    review["findings"],
                )
                for review in item.get("reviews", [])[-3:]
            )
            + (
                f"<details><summary>Planned change</summary>"
                f"<pre>{html.escape(item['change'] or '')}</pre></details>"
                if item["change"]
                else ""
            )
        )

    if research_error is not None:
        _content = mo.md(
            "Add the research workflow to `hiraku/workflows/research/` to see "
            f"the experiment tree here. ({research_error})"
        )
    elif overview is None:
        _content = mo.md("No task records yet.")
    else:
        _content = mo.vstack(
            [
                task_choice,
                _status(),
                research_chart,
                mo.hstack([research_pick], justify="start"),
                _card(selected_node),
            ]
        )
    section_body("research", _content)
    return


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
def _(section_heading):
    section_heading("tree", "Runs", open=True)
    return


@app.cell
def _(
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
def _(section_heading):
    section_heading("run", "Run")
    return


@app.cell
def _(
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
def _(section_heading):
    section_heading("plot", "Performance")
    return


@app.cell
def _(metrics, mo, tree_metric):
    metric = mo.ui.dropdown(metrics, value=tree_metric, label="Metric (y axis)")
    return (metric,)


@app.cell
def _(
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
