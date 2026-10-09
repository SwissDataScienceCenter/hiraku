"""Packaged Hiraku dashboard marimo application.

This notebook is served by ``hiraku dashboard`` in marimo application mode
(code hidden). It reads project context from environment variables set by the
launcher and never writes to research records.
"""

import os
from pathlib import Path

import marimo as mo
import pandas as pd

from hiraku.config import HirakuConfig
from hiraku.dashboard import build_snapshot, read_record_index


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
    try:
        snapshot = build_snapshot(
            config,
            record_id=record_id,
            scope=scope,
            use_active_record=record_id is not None,
        )
        return snapshot, None
    except Exception as exc:  # pragma: no cover - boundary for broken projects
        return None, str(exc)


def _records_frame(snapshot):
    return pd.DataFrame(
        [
            {
                "id": record.id,
                "parent": record.parent_id,
                "depth": record.depth,
                "type": record.type,
                "status": record.status,
            }
            for record in snapshot.records
        ]
    )


def _runs_frame(snapshot):
    if not snapshot.runs:
        return pd.DataFrame(columns=["id", "record", "timestamp", "commit"])
    rows = []
    for run in snapshot.runs:
        rows.append(
            {
                "id": run.id,
                "record": run.record_id,
                "timestamp": run.timestamp.isoformat() if run.timestamp else None,
                "commit": run.commit,
                **{f"metric.{key}": value for key, value in run.metrics.items()},
                **{f"param.{key}": value for key, value in run.params.items()},
            }
        )
    return pd.DataFrame(rows)


config = _load_config()
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

_ = refresh_button.value
snapshot, snapshot_error = _load_snapshot(record_select.value, scope_radio.value)

mo.stop(
    snapshot is None,
    mo.md(f"**Dashboard error:** {snapshot_error}"),
)

records_table = mo.ui.table(
    _records_frame(snapshot),
    selection=None,
    page_size=10,
    label="Records",
)
runs_table = mo.ui.table(
    _runs_frame(snapshot),
    selection="single",
    page_size=10,
    label="Runs",
)

selected_record = next(
    (record for record in snapshot.records if record.id == record_select.value),
    None,
)
if selected_record is None:
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

mo.vstack(
    [
        mo.md("# Hiraku dashboard"),
        mo.hstack([record_select, scope_radio, refresh_button]),
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
        runs_table,
    ]
)

app = mo.App(width="wide")
