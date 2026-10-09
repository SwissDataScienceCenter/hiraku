"""Bundled reactive dashboard. Start with hiraku dashboard."""

import marimo

__generated_with = "0.25.1"
app = marimo.App(width="wide")


@app.cell
def _():
    import os
    from pathlib import Path

    import altair as alt
    import marimo as mo

    from hiraku.config import HirakuConfig
    from hiraku.dashboard.branding import logo_data_uri, theme_css
    from hiraku.dashboard.plots import metric_direction, pareto_points, progress_points
    from hiraku.dashboard.session import DashboardSession
    from hiraku.dashboard.views import (
        metric_names,
        param_names,
        records_frame,
        runs_frame,
        sort_fields,
        sorted_runs,
    )

    config = HirakuConfig.load(Path(os.environ["HIRAKU_PROJECT_ROOT"]) / "hiraku.yml")
    session = DashboardSession(config)
    get_record, set_record = mo.state(
        os.environ.get("HIRAKU_DASHBOARD_RECORD") or config.active_record_id
    )
    return (
        alt,
        metric_direction,
        metric_names,
        logo_data_uri,
        mo,
        param_names,
        pareto_points,
        progress_points,
        records_frame,
        runs_frame,
        session,
        sort_fields,
        sorted_runs,
        theme_css,
        get_record,
        set_record,
    )


@app.cell
def _(logo_data_uri, mo, theme_css):
    _logo = logo_data_uri()
    _logo_white = logo_data_uri(reversed_logo=True)
    mo.Html(
        f"<style>{theme_css()}</style>"
        '<header class="hk-header"><div class="hk-shell hk-header-inner">'
        f'<img class="hk-brand-logo hk-logo-light" src="{_logo}" '
        'alt="Swiss Data Science Center">'
        f'<img class="hk-brand-logo hk-logo-dark" src="{_logo_white}" '
        'alt="Swiss Data Science Center">'
        '<div><h1 class="hk-brand-title">Hiraku dashboard</h1>'
        '<p class="hk-brand-subtitle">Research records and tracked runs</p>'
        "</div></div></header>"
    )
    return


@app.cell
def _(mo):
    refresh = mo.ui.refresh(
        options=["5s", "10s", "30s", "1m"], default_interval=None, label="Refresh"
    )
    scope = mo.ui.radio(
        ["Selected record's runs", "All attributed runs in this source"],
        value="Selected record's runs",
        label="Run scope",
    )
    mo.vstack([mo.hstack([refresh, scope])])
    return refresh, scope


@app.cell
def _(refresh, session):
    _ = refresh.value
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
def _(mo, record_select, scope, session):
    with mo.status.spinner(title="Fetching records and runs…"):
        result = session.refresh(
            record_select.value,
            "record" if scope.value == "Selected record's runs" else "source",
            use_active_record=False,
            cached=True,
        )
    mo.stop(result.snapshot is None, mo.md(f"Dashboard error: {result.error}"))
    snapshot = result.snapshot
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
                f"Source: {snapshot.source.label} · Scope: {snapshot.scope}"
                if snapshot.source
                else "Select a record to load its tracking source."
            ),
            *[mo.md(f"**{error.kind}:** {error.message}") for error in snapshot.errors],
        ]
    )
    return (snapshot,)


@app.cell
def _(mo, records_frame, snapshot):
    mo.vstack(
        [
            mo.md("## Records"),
            mo.ui.table(records_frame(snapshot), selection=None, page_size=10),
        ]
    )
    return


@app.cell
def _(mo, snapshot):
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
    mo.vstack(_detail)
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
def _(mo, runs_table, set_record, snapshot):
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
            record_link,
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
def _(metrics, mo, params):
    view = mo.ui.radio(["Progress", "Pareto"], value="Progress", label="Plot view")
    x_metric = mo.ui.dropdown(
        metrics,
        value=metrics[0] if metrics else None,
        allow_select_none=True,
        label="X / progress metric",
    )
    y_metric = mo.ui.dropdown(
        metrics,
        value=metrics[-1] if metrics else None,
        allow_select_none=True,
        label="Y metric",
    )
    cohort_field = mo.ui.dropdown(
        params, allow_select_none=True, label="Filter cohort by parameter"
    )
    mo.vstack([mo.md("## Plots"), view, mo.hstack([x_metric, y_metric, cohort_field])])
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
            .mark_point(filled=True)
            .encode(
                x=_x,
                y=alt.Y("value:Q", title=x_metric.value),
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
            .mark_point(filled=True)
            .encode(
                x=alt.X("x_original:Q", title=x_metric.value),
                y=alt.Y("y_original:Q", title=y_metric.value),
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
            plot,
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
