"""Packaged Hiraku dashboard marimo application.

This notebook is served by ``hiraku dashboard`` in marimo application mode
(code hidden). It reads project context from environment variables set by the
launcher and never writes to research records.
"""

import os
from pathlib import Path

import altair as alt
import marimo as mo

from hiraku.config import HirakuConfig
from hiraku.dashboard import read_record_index
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


def _project_root() -> Path | None:
    value = os.environ.get("HIRAKU_PROJECT_ROOT")
    return Path(value) if value else None


def _load_config() -> HirakuConfig:
    root = _project_root()
    if root is None:
        raise RuntimeError(
            "HIRAKU_PROJECT_ROOT is not set; launch the dashboard with "
            "`hiraku dashboard`."
        )
    marker = root / "hiraku.yml"
    if not marker.is_file():
        raise RuntimeError(f"No hiraku.yml found at {marker}")
    return HirakuConfig.load(marker)


def _load_snapshot(record_id, scope):
    result = session.refresh(
        record_id,
        scope,
        use_active_record=record_id is not None,
    )
    return result.snapshot, result.error


config = _load_config()
session = DashboardSession(config)
requested_record = os.environ.get("HIRAKU_DASHBOARD_RECORD") or None
requested_scope = os.environ.get("HIRAKU_DASHBOARD_SCOPE") or "record"
index, index_errors = read_record_index(config)
mo.stop(
    not index and not index_errors,
    mo.md("No research records found in this project."),
)

record_options = {record_id: record_id for record_id in sorted(index)}
record_select = mo.ui.dropdown(
    options=record_options,
    value=requested_record or config.active_record_id,
    allow_select_none=True,
    label="Record",
    full_width=True,
)
scope_radio = mo.ui.radio(
    {
        "record": "Selected record's runs",
        "source": "All attributed runs in this source",
    },
    value=requested_scope,
    label="Run scope",
)
refresh_button = mo.ui.button(label="Refresh")
auto_refresh = mo.ui.refresh(
    options=["5s", "10s", "30s", "1m"],
    default_interval=None,
    label="Auto-refresh",
)

_ = refresh_button.value
_ = auto_refresh.value
snapshot, snapshot_error = _load_snapshot(record_select.value, scope_radio.value)

mo.stop(snapshot is None, mo.md(f"**Dashboard error:** {snapshot_error}"))
if snapshot.stale:
    fetch_status = mo.md(
        "Showing the last successful snapshot; the latest fetch failed or "
        "was coalesced."
    )
else:
    fetch_status = mo.md(f"Last fetched at {snapshot.refreshed_at.isoformat()}")

backend_errors = [error for error in snapshot.errors if error.kind == "backend"]

if backend_errors:
    run_status = mo.md(
        "**Backend unavailable:** "
        + " ".join(error.message for error in backend_errors)
    )
elif snapshot.source is None:
    run_status = mo.md("No tracking source selected; choose a record above.")
elif not snapshot.runs:
    run_status = mo.md("No runs found in this scope.")
else:
    run_status = mo.md(
        f"Showing {len(snapshot.runs)} run(s) from **{snapshot.source.label}** "
        f"({snapshot.scope})."
    )

all_metrics = metric_names(snapshot)
all_params = param_names(snapshot)
metric_select = mo.ui.multiselect(
    options=all_metrics,
    value=all_metrics[:4],
    label="Metrics",
    full_width=True,
)
param_select = mo.ui.multiselect(
    options=all_params,
    value=all_params[:2],
    label="Parameters",
    full_width=True,
)
sort_select = mo.ui.dropdown(
    options={field: field for field in sort_fields(snapshot)},
    value="timestamp",
    label="Sort runs by",
    full_width=True,
)
ascending_checkbox = mo.ui.checkbox(value=True, label="Ascending")

ordered_runs = sorted_runs(
    snapshot, sort_select.value, ascending=ascending_checkbox.value
)
runs_table = mo.ui.table(
    runs_frame(
        ordered_runs,
        metrics=metric_select.value,
        params=param_select.value,
    ),
    selection="single",
    page_size=10,
    label="Runs",
)
records_table = mo.ui.table(
    records_frame(snapshot),
    selection=None,
    page_size=10,
    label="Records",
)

selected_record = next(
    (record for record in snapshot.records if record.id == record_select.value),
    None,
)
if selected_record is None:
    if record_select.value is not None:
        detail = mo.md(
            f"Selected record **{record_select.value}** is no longer present "
            "after refresh."
        )
    else:
        detail = mo.md("Select a record to inspect its details.")
else:
    inherited = [
        f"- **{key}** = `{value}` (from {source})"
        for key, value in selected_record.fields.items()
        if (source := selected_record.sources.get(key)) and source != selected_record.id
    ]
    local = [
        f"- **{key}** = `{value}`"
        for key, value in selected_record.fields.items()
        if selected_record.sources.get(key) == selected_record.id
    ]
    detail = mo.vstack(
        [
            mo.md(
                f"### {selected_record.id}\n"
                f"Type: {selected_record.type or '—'} · "
                f"Status: {selected_record.status or '—'} · "
                f"Commit: {selected_record.commit or '—'}"
            ),
            mo.md(selected_record.markdown or "_No description._"),
            mo.md("**Effective fields (explicit)**\n" + ("\n".join(local) or "_None_")),
            mo.md(
                "**Effective fields (inherited)**\n"
                + ("\n".join(inherited) or "_None_")
            ),
        ]
    )

errors_block = (
    mo.vstack(
        [
            mo.md("## Warnings"),
            *[mo.md(f"- `{error.kind}`: {error.message}") for error in snapshot.errors],
        ]
    )
    if snapshot.errors
    else mo.md("")
)

view_radio = mo.ui.radio(
    {"progress": "Progress", "pareto": "Pareto"},
    value="progress",
    label="Plot view",
)
progress_metric = mo.ui.dropdown(
    options={metric: metric for metric in all_metrics},
    value=all_metrics[0] if all_metrics else None,
    allow_select_none=True,
    label="Progress metric",
    full_width=True,
)
pareto_x = mo.ui.dropdown(
    options={metric: metric for metric in all_metrics},
    value=all_metrics[0] if all_metrics else None,
    allow_select_none=True,
    label="Pareto X metric",
    full_width=True,
)
pareto_y_value = all_metrics[0] if all_metrics else None
if len(all_metrics) > 1:
    pareto_y_value = all_metrics[1]
pareto_y = mo.ui.dropdown(
    options={metric: metric for metric in all_metrics},
    value=pareto_y_value,
    allow_select_none=True,
    label="Pareto Y metric",
    full_width=True,
)
pareto_x_direction = mo.ui.radio(
    {"minimize": "Minimize", "maximize": "Maximize"},
    value=metric_direction(snapshot, pareto_x.value) or "minimize",
    label="Pareto X direction",
)
pareto_y_direction = mo.ui.radio(
    {"minimize": "Minimize", "maximize": "Maximize"},
    value=metric_direction(snapshot, pareto_y.value) or "minimize",
    label="Pareto Y direction",
)

if view_radio.value == "progress":
    if progress_metric.value is None:
        plot = mo.md("No metric selected for the progress plot.")
    else:
        progress = progress_points(
            snapshot.runs,
            progress_metric.value,
            direction=metric_direction(snapshot, progress_metric.value),
        )
        frame = progress.frame
        if not frame.empty:
            x_kind = str(frame.iloc[0]["x_kind"])
            x_encoding = (
                alt.X("x:T", title="timestamp")
                if x_kind == "timestamp"
                else alt.X("x:Q", title="deterministic run index")
            )
            base = (
                alt.Chart(frame)
                .mark_line(point={"filled": True})
                .encode(
                    x=x_encoding,
                    y=alt.Y("value:Q", title=progress_metric.value),
                    tooltip=["run_id", "record_id", "commit", "value"],
                )
            )
            if "best_so_far" in frame.columns:
                overlay = (
                    alt.Chart(frame)
                    .mark_line(strokeDash=[4, 4], color="orange")
                    .encode(x=x_encoding, y="best_so_far:Q")
                )
                chart = base + overlay
            else:
                chart = base
            plot = mo.ui.altair_chart(chart, chart_selection="point", label="Progress")
        else:
            plot = mo.md("No plottable runs for this metric.")
else:
    if pareto_x.value is None or pareto_y.value is None:
        plot = mo.md("Select two metrics for the Pareto plot.")
    else:
        pareto = pareto_points(
            snapshot.runs,
            pareto_x.value,
            pareto_y.value,
            direction_x=pareto_x_direction.value,
            direction_y=pareto_y_direction.value,
        )
        if not pareto.frame.empty:
            chart = (
                alt.Chart(pareto.frame)
                .mark_point(filled=True)
                .encode(
                    x=alt.X("x_original:Q", title=pareto_x.value),
                    y=alt.Y("y_original:Q", title=pareto_y.value),
                    color=alt.Color(
                        "is_pareto:N",
                        legend=alt.Legend(title="Pareto frontier"),
                    ),
                    tooltip=[
                        "run_id",
                        "record_id",
                        "commit",
                        "x_original",
                        "y_original",
                    ],
                )
            )
            plot = mo.ui.altair_chart(chart, chart_selection="point", label="Pareto")
        else:
            plot = mo.md("No plottable runs for these metrics.")

selected_plot_rows = getattr(plot, "value", None)
if selected_plot_rows is None:
    selected_plot_rows = []
plot_selection = mo.ui.table(
    selected_plot_rows,
    selection=None,
    label="Selected plot points",
)

mo.vstack(
    [
        mo.md("# Hiraku dashboard"),
        mo.hstack([record_select, scope_radio, refresh_button, auto_refresh]),
        fetch_status,
        mo.md(
            f"Source: **{snapshot.source.label}** ({snapshot.scope})"
            if snapshot.source
            else "No tracking source selected."
        ),
        mo.hstack(
            [
                mo.vstack([mo.md("## Records"), records_table]),
                mo.vstack([mo.md("## Record detail"), detail]),
            ],
            widths=[1, 1],
        ),
        mo.md("## Runs"),
        mo.hstack(
            [sort_select, ascending_checkbox, metric_select, param_select],
            widths=[1, 0.5, 1, 1],
        ),
        run_status,
        runs_table,
        mo.md("## Plots"),
        view_radio,
        mo.hstack(
            [progress_metric, pareto_x, pareto_y],
            widths=[1, 1, 1],
        ),
        mo.hstack([pareto_x_direction, pareto_y_direction]),
        plot,
        plot_selection,
        errors_block,
    ]
)

app = mo.App(width="wide")
