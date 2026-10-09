"""Bundled reactive dashboard. Start with hiraku dashboard."""

import marimo

__generated_with = "0.25.1"
app = marimo.App(width="wide")


@app.cell
def _():
    import atexit
    import os
    from pathlib import Path

    import altair as alt
    import marimo as mo

    from hiraku.config import HirakuConfig
    from hiraku.dashboard.actions import (
        RecordDraft,
        RecordDraftError,
        create_record,
        validate_record_draft,
    )
    from hiraku.dashboard.branding import logo_data_uri, theme_css
    from hiraku.dashboard.capability import DashboardCapability
    from hiraku.dashboard.graph import RecordGraph, tree_payload
    from hiraku.dashboard.jobs import LaunchController
    from hiraku.dashboard.plots import metric_direction, pareto_points, progress_points
    from hiraku.dashboard.session import DashboardSession
    from hiraku.dashboard.tree import compute_layout, reconcile_navigation
    from hiraku.dashboard.views import (
        backend_action_label,
        default_plot_metrics,
        metric_names,
        param_names,
        records_frame,
        runs_frame,
        safe_run_url,
        sort_fields,
        sorted_runs,
    )

    config = HirakuConfig.load(Path(os.environ["HIRAKU_PROJECT_ROOT"]) / "hiraku.yml")
    session = DashboardSession(config)
    capability = DashboardCapability(
        interactive=os.environ.get("HIRAKU_DASHBOARD_INTERACTIVE") == "1"
    )
    controller = LaunchController(config, capability)
    atexit.register(controller.shutdown)
    get_record, set_record = mo.state(
        os.environ.get("HIRAKU_DASHBOARD_RECORD") or config.active_record_id
    )
    get_expanded, set_expanded = mo.state([])
    get_action_refresh, set_action_refresh = mo.state(0)
    get_form_submit, set_form_submit = mo.state(None)
    get_form_handled, set_form_handled = mo.state(None)
    get_form_busy, set_form_busy = mo.state(False)
    get_form_result, set_form_result = mo.state(None)
    get_form_error, set_form_error = mo.state(None)
    get_preview_request, set_preview_request = mo.state(None)
    get_preview_handled, set_preview_handled = mo.state(None)
    get_preview_result, set_preview_result = mo.state(None)
    get_reset_version, set_reset_version = mo.state(0)
    return (
        alt,
        backend_action_label,
        capability,
        config,
        controller,
        default_plot_metrics,
        metric_direction,
        metric_names,
        logo_data_uri,
        mo,
        param_names,
        pareto_points,
        progress_points,
        records_frame,
        runs_frame,
        safe_run_url,
        session,
        sort_fields,
        sorted_runs,
        theme_css,
        compute_layout,
        reconcile_navigation,
        RecordGraph,
        tree_payload,
        get_record,
        set_record,
        get_expanded,
        set_expanded,
        get_action_refresh,
        set_action_refresh,
        get_form_submit,
        set_form_submit,
        get_form_handled,
        set_form_handled,
        get_form_busy,
        set_form_busy,
        get_form_result,
        set_form_result,
        get_form_error,
        set_form_error,
        get_preview_request,
        set_preview_request,
        get_preview_handled,
        set_preview_handled,
        get_preview_result,
        set_preview_result,
        get_reset_version,
        set_reset_version,
        RecordDraft,
        RecordDraftError,
        create_record,
        validate_record_draft,
    )


@app.cell
def _(capability, logo_data_uri, mo, theme_css):
    _logo = logo_data_uri()
    _logo_white = logo_data_uri(reversed_logo=True)
    _mode = "Interactive" if capability.interactive else "Read-only"
    mo.Html(
        f"<style>{theme_css()}</style>"
        '<header class="hk-header"><div class="hk-shell hk-header-inner">'
        f'<img class="hk-brand-logo hk-logo-light" src="{_logo}" '
        'alt="Swiss Data Science Center">'
        f'<img class="hk-brand-logo hk-logo-dark" src="{_logo_white}" '
        'alt="Swiss Data Science Center">'
        '<div><h1 class="hk-brand-title">Hiraku dashboard</h1>'
        f'<p class="hk-brand-subtitle">Research records and tracked runs · {_mode}</p>'
        "</div></div></header>"
    )
    return


@app.cell
def _(mo):
    import os as _os

    refresh = mo.ui.refresh(
        options=["5s", "10s", "30s", "1m"], default_interval=None, label="Refresh"
    )
    scope_labels = [
        "This record",
        "This subtree",
        "All attributed runs in this source",
    ]
    _initial_scope = _os.environ.get("HIRAKU_DASHBOARD_SCOPE") or "record"
    scope = mo.ui.radio(
        scope_labels,
        value={
            "record": "This record",
            "subtree": "This subtree",
            "source": "All attributed runs in this source",
        }[_initial_scope],
        label="Run scope",
    )
    mo.vstack([mo.hstack([refresh, scope])])
    return refresh, scope


@app.cell
def _(get_action_refresh, refresh, session):
    _ = refresh.value
    _ = get_action_refresh()
    record_ids = session.reload_records()
    return (record_ids,)


@app.cell
def _(get_record, mo, record_ids, set_record):
    _selected = get_record()
    _ids = list(record_ids)
    # Keep disappeared selections visible so they produce an explicit error.
    if _selected and _selected not in _ids:
        _ids.append(_selected)
    record_select = mo.ui.dropdown(
        _ids,
        value=_selected,
        allow_select_none=True,
        searchable=True,
        label="Record",
        on_change=set_record,
    )
    mo.vstack([record_select])
    return (record_select,)


@app.cell
def _(
    get_form_busy,
    get_record,
    get_reset_version,
    mo,
    record_ids,
    set_form_busy,
    set_form_submit,
    set_preview_request,
    set_reset_version,
):
    import uuid as _uuid

    _ = get_reset_version()
    _selected = get_record()
    _parent_options = ["No parent (root)", *record_ids]
    parent = mo.ui.dropdown(
        _parent_options,
        value=_selected if _selected in _parent_options else "No parent (root)",
        label="Parent",
        full_width=True,
    )
    record_id_input = mo.ui.text(label="Record ID", value="", full_width=True)
    type_input = mo.ui.text(
        label="Type",
        value="",
        placeholder="task / experiment / custom",
        full_width=True,
    )
    summary_input = mo.ui.text_area(label="Summary", value="", full_width=True)
    goal_input = mo.ui.text_area(label="Goal", value="", full_width=True)
    question_input = mo.ui.text_area(label="Question", value="", full_width=True)
    hypothesis_input = mo.ui.text_area(label="Hypothesis", value="", full_width=True)
    commit_input = mo.ui.text(label="Commit ref (optional)", value="", full_width=True)
    related_input = mo.ui.text(
        label="Related IDs (comma separated)", value="", full_width=True
    )
    markdown_input = mo.ui.text_area(label="Markdown body", value="", full_width=True)
    advanced_yaml = mo.ui.text_area(
        label="Advanced local YAML",
        value="",
        full_width=True,
        rows=6,
    )
    _advanced_help = mo.md(
        "Advanced fields accept a YAML mapping of additional local fields. "
        "Example mappings:\n\n"
        "```yaml\n"
        "entrypoints:\n"
        "  train:\n"
        "    command: [uv, run, python, train.py]\n"
        "    args: {{epochs: 10}}\n"
        "metrics:\n"
        "  - name: val/accuracy\n"
        "    direction: maximize\n"
        "tracking_backend: {{type: csv}}\n"
        "```"
    )

    def _submit(values):
        set_form_busy(True)
        set_form_submit({"event_id": _uuid.uuid4().hex, "values": values or {}})

    form = (
        mo.vstack(
            [
                record_id_input,
                parent,
                type_input,
                summary_input,
                goal_input,
                question_input,
                hypothesis_input,
                mo.accordion(
                    {
                        "Commit, links, Markdown and advanced YAML": mo.vstack(
                            [
                                commit_input,
                                related_input,
                                markdown_input,
                                _advanced_help,
                                advanced_yaml,
                            ]
                        )
                    }
                ),
            ],
            align="stretch",
            gap=0.5,
        )
        .batch(
            id=record_id_input,
            parent=parent,
            type=type_input,
            summary=summary_input,
            goal=goal_input,
            question=question_input,
            hypothesis=hypothesis_input,
            commit=commit_input,
            related=related_input,
            markdown=markdown_input,
            advanced_yaml=advanced_yaml,
        )
        .form(
            submit_button_label="Create",
            submit_button_disabled=get_form_busy(),
            on_change=_submit,
        )
    )
    preview_button = mo.ui.button(
        label="Preview",
        on_click=lambda _: set_preview_request(_uuid.uuid4().hex),
    )
    cancel_button = mo.ui.button(
        label="Cancel",
        on_click=lambda _: set_reset_version(get_reset_version() + 1),
    )
    mo.vstack(
        [
            mo.md("## Create record"),
            mo.md(
                "Create a research root or a child experiment. No project files "
                "are written until **Create** is pressed."
            ),
            mo.hstack(
                [form, mo.vstack([preview_button, cancel_button])],
                align="start",
                gap=1.0,
                wrap=True,
            ),
        ]
    )
    return form, preview_button, cancel_button


@app.cell
def _(
    capability,
    config,
    get_action_refresh,
    get_form_handled,
    get_form_submit,
    get_reset_version,
    mo,
    RecordDraft,
    RecordDraftError,
    create_record,
    set_action_refresh,
    set_form_busy,
    set_form_error,
    set_form_handled,
    set_form_result,
    set_record,
    set_reset_version,
):
    submission = get_form_submit()
    mo.stop(submission is None or submission["event_id"] == get_form_handled())
    set_form_handled(submission["event_id"])
    try:
        values = submission["values"] or {}
        _parent = values.get("parent") or "No parent (root)"
        _draft = RecordDraft(
            id=str(values.get("id") or ""),
            parent_id=None if _parent == "No parent (root)" else _parent,
            related_to=tuple(
                item.strip()
                for item in str(values.get("related") or "").split(",")
                if item.strip()
            ),
            type=values.get("type") or None,
            commit_ref=values.get("commit") or None,
            markdown=values.get("markdown") or "",
            summary=values.get("summary") or None,
            goal=values.get("goal") or None,
            question=values.get("question") or None,
            hypothesis=values.get("hypothesis") or None,
            advanced_yaml=values.get("advanced_yaml") or "",
        )
        _created = create_record(config, _draft, capability)
    except RecordDraftError as exc:
        set_form_error({"message": str(exc), "errors": exc.errors})
        set_form_result(None)
    except Exception as exc:
        set_form_error({"message": str(exc), "errors": {"_submit": str(exc)}})
        set_form_result(None)
    else:
        set_form_result({"path": str(_created.path), "id": _created.record.id})
        set_form_error(None)
        set_record(_created.record.id)
        set_reset_version(get_reset_version() + 1)
        set_action_refresh(get_action_refresh() + 1)
    finally:
        set_form_busy(False)


@app.cell
def _(
    config,
    form,
    get_preview_handled,
    get_preview_request,
    mo,
    RecordDraft,
    set_preview_handled,
    set_preview_result,
    validate_record_draft,
):
    request_id = get_preview_request()
    mo.stop(request_id is None or request_id == get_preview_handled())
    set_preview_handled(request_id)
    try:
        _elements = form.element.elements
        _values = {key: element.value for key, element in _elements.items()}
        _parent = _values.get("parent") or "No parent (root)"
        _draft = RecordDraft(
            id=str(_values.get("id") or ""),
            parent_id=None if _parent == "No parent (root)" else _parent,
            related_to=tuple(
                item.strip()
                for item in str(_values.get("related") or "").split(",")
                if item.strip()
            ),
            type=_values.get("type") or None,
            commit_ref=_values.get("commit") or None,
            markdown=_values.get("markdown") or "",
            summary=_values.get("summary") or None,
            goal=_values.get("goal") or None,
            question=_values.get("question") or None,
            hypothesis=_values.get("hypothesis") or None,
            advanced_yaml=_values.get("advanced_yaml") or "",
        )
        _preview = validate_record_draft(config, _draft)
    except Exception as exc:
        set_preview_result(
            {"path": None, "errors": {"_preview": str(exc)}, "fields": {}}
        )
    else:
        set_preview_result(
            {
                "path": str(_preview.path) if _preview.path else None,
                "errors": _preview.errors,
                "fields": _preview.fields,
            }
        )


@app.cell
def _(get_form_error, get_form_result, get_preview_result, mo):
    _messages = []
    _result = get_form_result()
    if _result:
        _messages.append(
            mo.md(f"Created record **{_result['id']}** at `{_result['path']}`.")
        )
    _error = get_form_error()
    if _error:
        _messages.append(mo.md(f"**Create failed:** {_error['message']}"))
        _messages.extend(
            mo.md(f"**{key}:** {value}") for key, value in _error["errors"].items()
        )
    _preview = get_preview_result()
    if _preview:
        if _preview["errors"]:
            _messages.append(mo.md("**Preview failed:**"))
            _messages.extend(
                mo.md(f"**{key}:** {value}")
                for key, value in _preview["errors"].items()
            )
        elif _preview["path"]:
            _messages.append(
                mo.md(
                    f"Preview: record **{_preview['fields'].get('id') or '—'}** "
                    f"would be written to `{_preview['path']}`."
                )
            )
    mo.vstack(_messages) if _messages else mo.md("")


@app.cell
def _(mo, record_select, scope, session):
    _scope_value = {
        "This record": "record",
        "This subtree": "subtree",
        "All attributed runs in this source": "source",
    }[scope.value]
    with mo.status.spinner(title="Fetching records and runs…"):
        result = session.refresh(
            record_select.value,
            _scope_value,
            use_active_record=False,
            cached=True,
        )
    mo.stop(result.snapshot is None, mo.md(f"Dashboard error: {result.error}"))
    snapshot = result.snapshot
    if session.selected_run and session.selected_run not in {
        run.key for run in snapshot.runs
    }:
        session.selected_run = None
    _coverage = "; ".join(
        f"{source.label}: {source.run_count or 0} runs" for source in snapshot.sources
    )
    if any(error.kind == "backend" for error in snapshot.errors):
        _coverage = "Partial / unavailable — " + (_coverage or "no sources loaded")
    mo.vstack(
        [
            mo.md(
                (
                    "Fetch failed: "
                    if result.error and not snapshot.stale
                    else "Last successful fetch: "
                )
                + snapshot.refreshed_at.isoformat()
                + (" — **STALE**" if snapshot.stale else "")
            ),
            mo.md(result.error or ""),
            mo.md(
                f"Scope: {scope.value} · Coverage: {_coverage}"
                if snapshot.selected_record_id
                else "Select a record to load its tracking source."
            ),
            *[mo.md(f"**{error.kind}:** {error.message}") for error in snapshot.errors],
        ]
    )
    return (snapshot,)


@app.cell
def _(RecordGraph, mo):
    # Keep the widget alive across navigation so search, focus and zoom survive.
    graph = mo.ui.anywidget(
        RecordGraph(
            tree=[],
            height=380,
        )
    )
    return (graph,)


@app.cell
def _(graph, set_expanded, set_record):
    _selected = graph.value.get("selected") or None
    if _selected:
        set_record(_selected)
    _expanded = list(graph.value.get("expanded") or [])
    set_expanded(_expanded)
    return


@app.cell
def _(
    compute_layout,
    get_expanded,
    get_record,
    graph,
    mo,
    reconcile_navigation,
    snapshot,
    tree_payload,
):
    _nav = reconcile_navigation(
        snapshot.records,
        selected_id=get_record(),
        expanded=get_expanded(),
    )
    _layout = compute_layout(
        snapshot.records,
        selected_id=_nav.selected_id,
        expanded=_nav.expanded,
        search=graph.value.get("query") or "",
    )
    with graph.widget.hold_sync():
        graph.tree = tree_payload(_layout)
        graph.selected = _nav.selected_id or ""
        graph.expanded = sorted(_nav.expanded)
    _message = (
        "Previously selected record is missing or malformed."
        if _nav.removed_selection
        else ""
    )
    mo.vstack([mo.md(f"**{_message}**" if _message else "")])
    return (_layout,)


@app.cell
def _(mo, snapshot):
    _by_id = {record.id: record for record in snapshot.records}
    _path: list[str] = []
    _current = snapshot.selected_record_id
    _seen: set[str] = set()
    while _current and _current in _by_id and _current not in _seen:
        _path.append(_current)
        _seen.add(_current)
        _current = _by_id[_current].parent_id
    breadcrumb = mo.md(" · ".join(reversed(_path)) if _path else "No record selected")
    return (breadcrumb,)


@app.cell
def _(breadcrumb, graph, mo, snapshot):
    _record = next(
        (r for r in snapshot.records if r.id == snapshot.selected_record_id), None
    )
    _detail = [mo.md("## Record detail")]
    if _record:
        _detail.extend(
            [
                mo.md(
                    f"### {_record.id}\nType: {_record.type or '—'} · "
                    f"Status: {_record.status or '—'} · Commit: {_record.commit or '—'}"
                ),
                mo.md(_record.markdown or "_No description._"),
                mo.ui.table(
                    [
                        {
                            "field": k,
                            "value": str(v),
                            "defined by": str(_record.sources.get(k)),
                            "origin": "explicit"
                            if _record.sources.get(k) == _record.id
                            else "inherited / merged",
                        }
                        for k, v in _record.fields.items()
                    ],
                    selection=None,
                ),
            ]
        )
    else:
        _detail.append(mo.md("No record selected."))
    mo.hstack(
        [
            mo.vstack([mo.md("## Records"), breadcrumb, graph]).style(
                {"flex": "1 1 360px", "min-width": "0"}
            ),
            mo.vstack(_detail).style({"flex": "1 1 360px", "min-width": "0"}),
        ],
        align="start",
        wrap=True,
        gap=1.5,
    )
    return


@app.cell
def _(metric_names, mo, param_names, snapshot, sort_fields):
    metrics = metric_names(snapshot)
    params = param_names(snapshot)
    metric_select = mo.ui.multiselect(metrics, value=metrics[:4], label="Metrics")
    param_select = mo.ui.multiselect(params, value=params[:2], label="Parameters")
    sort_select = mo.ui.dropdown(sort_fields(snapshot), value="timestamp", label="Sort")
    ascending = mo.ui.checkbox(value=True, label="Ascending")
    mo.vstack(
        [
            mo.md("## Runs"),
            mo.hstack([metric_select, param_select, sort_select, ascending]),
        ]
    )
    return ascending, metric_select, metrics, param_select, params, sort_select


@app.cell
def _(
    ascending,
    metric_select,
    mo,
    param_select,
    runs_frame,
    session,
    snapshot,
    sort_select,
    sorted_runs,
):
    _runs = sorted_runs(snapshot, sort_select.value, ascending=ascending.value)
    runs_table = mo.ui.table(
        runs_frame(_runs, metrics=metric_select.value, params=param_select.value),
        selection="single",
        page_size=10,
        label="Runs",
        initial_selection=[
            i for i, run in enumerate(_runs) if run.key == session.selected_run
        ],
        on_change=session.select_run,
        hidden_columns=["key"],
    )
    mo.vstack([mo.md("No runs found in this scope." if not _runs else ""), runs_table])
    return (runs_table,)


@app.cell
def _(
    backend_action_label,
    mo,
    runs_table,
    safe_run_url,
    set_record,
    snapshot,
):
    import html as _html

    _rows = runs_table.value
    _run = next(
        (r for r in snapshot.runs if len(_rows) and r.key == _rows.iloc[0]["key"]), None
    )
    record_link = (
        mo.ui.button(
            label=f"Open record {_run.record_id}",
            on_click=lambda _: set_record(_run.record_id),
        )
        if _run and _run.record_id in {r.id for r in snapshot.records}
        else mo.md("")
    )
    if _run:
        _source = next(
            (s for s in snapshot.sources if s.identity == _run.source),
            snapshot.source,
        )
        _action_label = backend_action_label(_source.name if _source else None)
        _link_url = safe_run_url(_run.url)
        if _link_url:
            _action = mo.Html(
                '<a class="hk-run-link" href="'
                + _html.escape(_link_url, quote=True)
                + '" target="_blank" rel="noopener noreferrer">'
                + _html.escape(_action_label)
                + "</a>"
            )
        else:
            _action = mo.md(
                "No safe external link is available for this run; "
                "configure `ui_url` for MLflow or add a CSV `url` column."
            )
    else:
        _action = mo.md("")
    mo.vstack(
        [
            mo.md("### Run provenance"),
            mo.md(
                f"Run: {_run.id} · "
                f"Record: {_run.record_id or 'metadata unavailable'} · "
                f"Commit: {_run.commit or 'metadata unavailable'}"
            )
            if _run
            else mo.md("Select a run to inspect its provenance."),
            mo.hstack([record_link, _action]) if _run else mo.md(""),
            mo.ui.table(
                [{"field": k, "value": str(v)} for k, v in vars(_run).items()],
                selection=None,
            )
            if _run
            else mo.md(""),
        ]
    )
    return (record_link,)


@app.cell
def _(default_plot_metrics, metrics, mo, params, snapshot):
    view = mo.ui.radio(["Progress", "Pareto"], value="Progress", label="Plot view")
    _default_x, _default_y = default_plot_metrics(snapshot)
    x_metric = mo.ui.dropdown(
        metrics,
        value=_default_x,
        allow_select_none=True,
        label="X / progress metric",
    )
    y_metric = mo.ui.dropdown(
        metrics,
        value=_default_y,
        allow_select_none=True,
        label="Y metric",
    )
    cohort_field = mo.ui.dropdown(
        params, allow_select_none=True, label="Filter cohort by parameter"
    )
    mo.vstack(
        [
            mo.md("## Plots"),
            mo.md(
                "Plots compare final metrics across runs. "
                "Each point represents one run; epoch histories are in your tracker."
            ),
            view,
            mo.hstack([x_metric, y_metric, cohort_field]),
        ]
    )
    return cohort_field, view, x_metric, y_metric


@app.cell
def _(cohort_field, mo, snapshot):
    _values = sorted({str(r.params.get(cohort_field.value)) for r in snapshot.runs})
    cohort_value = mo.ui.dropdown(_values, allow_select_none=True, label="Cohort value")
    cohort_value if cohort_field.value else mo.md("")
    return (cohort_value,)


@app.cell
def _(metric_direction, mo, snapshot, x_metric, y_metric):
    x_direction = mo.ui.dropdown(
        ["minimize", "maximize"],
        value=metric_direction(snapshot, x_metric.value),
        allow_select_none=True,
        label="X direction",
    )
    y_direction = mo.ui.dropdown(
        ["minimize", "maximize"],
        value=metric_direction(snapshot, y_metric.value),
        allow_select_none=True,
        label="Y direction",
    )
    mo.hstack([x_direction, y_direction])
    return x_direction, y_direction


@app.cell
def _(
    alt,
    cohort_field,
    cohort_value,
    mo,
    pareto_points,
    progress_points,
    snapshot,
    view,
    x_direction,
    x_metric,
    y_direction,
    y_metric,
):
    _runs = [
        r
        for r in snapshot.runs
        if not cohort_field.value
        or cohort_value.value is None
        or str(r.params.get(cohort_field.value)) == cohort_value.value
    ]
    mo.stop(x_metric.value is None, mo.md("No metric selected."))
    if view.value == "Progress":
        _data = progress_points(_runs, x_metric.value, direction=x_direction.value)
        _frame = _data.frame
        mo.stop(_frame.empty, mo.md(f"No plottable runs; omitted {_data.omitted}."))
        _x = (
            alt.X(
                "x:T",
                title="Run timestamp",
                scale=alt.Scale(
                    domain=[
                        float(_frame["x"].min()) - 1000,
                        float(_frame["x"].max()) + 1000,
                    ]
                ),
            )
            if _frame.iloc[0]["x_kind"] == "timestamp"
            else alt.X("x:Q", title="Deterministic run index")
        )
        _chart = (
            alt.Chart(alt.InlineData(values=_frame.to_dict("records")))
            .mark_point(filled=True, size=100)
            .encode(
                x=_x,
                y=alt.Y("value:Q", title=x_metric.value, scale=alt.Scale(padding=12)),
                tooltip=["run_id:N", "record_id:N", "commit:N", "value:Q"],
            )
        )
        if "best_so_far" in _frame:
            _chart += (
                alt.Chart(alt.InlineData(values=_frame.to_dict("records")))
                .mark_line(strokeDash=[4, 4])
                .encode(x=_x, y="best_so_far:Q")
            )
    else:
        mo.stop(
            not y_metric.value or not x_direction.value or not y_direction.value,
            mo.md("Select both metrics and explicit optimization directions."),
        )
        _data = pareto_points(
            _runs,
            x_metric.value,
            y_metric.value,
            direction_x=x_direction.value,
            direction_y=y_direction.value,
        )
        mo.stop(
            _data.frame.empty, mo.md(f"No plottable runs; omitted {_data.omitted}.")
        )
        _chart = (
            alt.Chart(alt.InlineData(values=_data.frame.to_dict("records")))
            .mark_point(filled=True, size=100)
            .encode(
                x=alt.X(
                    "x_original:Q", title=x_metric.value, scale=alt.Scale(padding=12)
                ),
                y=alt.Y(
                    "y_original:Q", title=y_metric.value, scale=alt.Scale(padding=12)
                ),
                color=alt.Color(
                    "is_pareto:N",
                    scale=alt.Scale(domain=[True, False], range=["#26245c", "#56b4e9"]),
                    legend=alt.Legend(title="Pareto frontier"),
                ),
                tooltip=[
                    "run_id:N",
                    "record_id:N",
                    "commit:N",
                    "x_original:Q",
                    "y_original:Q",
                ],
            )
        )
    plot_frame = _data.frame
    _chart = _chart.properties(width=600, height=320)
    # Inline records avoid browser-dependent temporal parsing in Arrow transport.
    plot = mo.ui.altair_chart(_chart, chart_selection="point", label=view.value)
    mo.vstack(
        [
            mo.md(
                f"Comparison cohort: {len(_runs)} runs · {snapshot.scope} scope · "
                f"{cohort_field.value or 'all protocols'} = "
                f"{cohort_value.value or 'all'}\n\n"
                "Matching metric names do not establish comparable "
                "evaluation protocols."
            ),
            mo.md(f"Omitted {_data.omitted} runs: {_data.omitted_reason}."),
            plot.style({"max-width": "100%", "overflow-x": "auto"}),
        ]
    )
    return plot, plot_frame


@app.cell
def _(mo, plot, plot_frame):
    mo.vstack(
        [
            mo.md("### Selected plot points"),
            mo.ui.table(plot.apply_selection(plot_frame), selection=None),
        ]
    )
    return


@app.cell
def _(logo_data_uri, mo):
    mo.Html(
        '<footer class="hk-footer"><div class="hk-shell hk-footer-inner">'
        '<img src="' + logo_data_uri(reversed_logo=True) + '" '
        'alt="Swiss Data Science Center">'
        '<p class="hk-footer-label">Swiss Data Science Center</p>'
        "</div></footer>"
    )
    return


if __name__ == "__main__":
    app.run()
