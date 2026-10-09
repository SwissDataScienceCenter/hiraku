# Dashboard

`hiraku dashboard` serves a local, view-only marimo application for browsing
research records and tracked runs. It is designed for inspection and
provenance review; it does not edit records, launch entrypoints, or chat with
agents.

## Installation

The dashboard is an optional extra so the core CLI stays lightweight:

```bash
uv sync --extra dashboard
# or, for an installed package
uv tool install "hiraku[dashboard]"
```

The command prints an install hint if the extra is missing. It never installs
packages automatically.

## Commands

```bash
hiraku dashboard
hiraku dashboard --record child-a
hiraku dashboard --port 8765 --no-browser
hiraku dashboard --notebook path/to/custom_notebook.py
```

- The server binds to `127.0.0.1` only; there is no shared-host deployment.
- `--record` selects the initial record. Without it, the persisted active
  record is used when present; otherwise the record browser is shown and no
  tracking source is queried until you select a record.
- `--no-browser` starts the server without opening a browser.
- `--notebook` runs an explicitly selected user-owned notebook instead of the
  bundled application. Default startup never discovers notebooks from the
  project automatically.

## Run scopes

- **Selected record's runs** queries the selected record's resolved tracking
  source for runs attributed to that record ID.
- **All attributed runs in this source** queries the same source once for
  every run that carries Hiraku record attribution. It is not a
  descendant-tree query.

Runs are identified by `(source identity, run ID)` so identical run IDs from
different sources never collide.

## Plots

- **Progress** plots one summary metric per run against timestamps, or a
  clearly labelled deterministic run index when timestamps are missing. A
  best-so-far overlay appears only when the metric direction is declared.
- **Pareto** plots two selected metrics and highlights the nondominated
  frontier. Directions default to the selected record's metric declarations;
  otherwise they default to *minimize* and must be set explicitly.
- Missing and non-finite values are excluded from calculations with a visible
  omitted-row count and are never treated as zero.

## Read-only guarantee

The bundled dashboard only reads records and tracking sources:

- it uses a non-mutating record index and never runs reparent recovery;
- browsing or selecting records never changes the CLI active record;
- sorting, chart switching, and point selection operate on the cached snapshot
  without re-querying the tracker;
- marimo session/cache files are kept in a temporary directory outside the
  research records.

A custom notebook is trusted executable code. The view-only guarantee applies
to the shipped dashboard, not to arbitrary user code.

## Custom notebook interface

A custom notebook receives project context through environment variables:

- `HIRAKU_PROJECT_ROOT` — absolute project root;
- `HIRAKU_DASHBOARD_RECORD` — initial record ID, empty when unset;
- `HIRAKU_DASHBOARD_SCOPE` — `record` or `source`.

Use the snapshot API for backend-agnostic rendering:

```python
import marimo as mo
from hiraku.config import HirakuConfig
from hiraku.dashboard import build_snapshot
from hiraku.dashboard.plots import progress_points

config = HirakuConfig.load(Path(__import__("os").environ["HIRAKU_PROJECT_ROOT"]) / "hiraku.yml")
snapshot = build_snapshot(config)
progress = progress_points(snapshot.runs, "loss", direction="minimize")
mo.ui.altair_chart(progress.frame)
```

## Troubleshooting

- **`No hiraku.yml found`** — run the command from inside a Hiraku project or
  initialize one first.
- **`The dashboard requires the optional 'dashboard' dependencies`** — run
  `uv sync --extra dashboard`.
- **`Port must be an integer between 1 and 65535`** — choose another `--port`.
- **`Backend unavailable`** — the tracking source could not be read. Fix the
  backend configuration or storage, then press *Refresh*.
