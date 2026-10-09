# Dashboard

`hiraku dashboard` serves a local marimo application for browsing research
records and tracked runs. The default mode is read-only: it does not edit
records, launch entrypoints, or chat with agents. With `--interactive`, the
same bundled app adds authenticated record creation and entrypoint launches
while all default browsing behavior stays read-only.

The bundled app loads a reusable SDSC theme from
`hiraku.dashboard/theme.css`. Its CSS custom properties and `.hk-*` component
classes can be reused by dashboard views. The app includes the supplied
primary SDSC logo in its header and the footer branding. The provided logo
archive did not contain the four partner logo files named in the UI kit, so
those marks are not currently shown.

## Installation

The dashboard is an optional extra so the core CLI stays lightweight:

```bash
uv sync --extra dashboard
# or, for an installed package
uv tool install "hiraku[dashboard]"
```

The command prints an install hint if the extra is missing. It never installs
packages automatically.

Hiraku supports Python 3.11 and newer. Install the optional tracking extras
(`mlflow` or `wandb`) as well when using those sources.

## Commands

```bash
hiraku dashboard
hiraku dashboard --interactive
hiraku dashboard --record child-a
hiraku dashboard --record child-a --scope subtree
hiraku dashboard --record child-a --scope source
hiraku dashboard --port 8765 --no-browser
hiraku dashboard --notebook path/to/custom_notebook.py
```

- The server binds to `127.0.0.1` only; there is no shared-host deployment.
- `--interactive` enables the **Create record** and **Launch run** panels.
  The launcher always forwards `HIRAKU_DASHBOARD_INTERACTIVE=1` or `0`, so an
  inherited environment variable cannot enable writes accidentally.
- Interactive startup uses marimo token authentication and prints the token
  URL to the terminal. The token is never logged by Hiraku. Default startup
  keeps `--no-token` for local read-only browsing.
- `--interactive --notebook ...` is rejected in this increment; custom
  notebook capability contracts are separate from the bundled interactive app.
- `--record` selects the initial record. Without it, the persisted active
  record is used when present; otherwise the record browser is shown and no
  tracking source is queried until you select a record.
- `--scope subtree` initially shows the selected record and its descendants
  across their effective tracking sources. `--scope source` compares all
  attributed runs in the selected source. The default scope remains `record`.
- `--no-browser` starts the server without opening a browser.
- `--notebook` runs an explicitly selected user-owned notebook instead of the
  bundled application. Default startup never discovers notebooks from the
  project automatically.

## Run scopes

- **This record** queries the selected record's resolved tracking source for
  runs attributed to that record ID.
- **This subtree** resolves the selected record plus its descendants, groups
  members by their effective source identity, queries each distinct source once
  per refresh, and keeps only runs attributed to those members.
- **All attributed runs in this source** queries the selected record's source
  once for every run that carries Hiraku record attribution. It is not a
  descendant-tree query.

Runs are identified by `(source identity, run ID)` so identical run IDs from
different sources never collide. Partial subtree failures keep the successful
sources visible and are reported explicitly.

The record graph and detail panel share selection with the record dropdown and
run's **Open record** action. Selecting a child reveals its ancestors. Search,
expansion and pan/zoom survive record navigation; the panels wrap vertically on
narrow screens. Source scope also keeps historical attributed runs whose record
files are missing; their record-navigation action is unavailable.

For the retained Digits demo, run this from the repository to restart both the
dashboard and MLflow UI without retraining:

```bash
uv run --all-extras python scripts/test_dashboard.py --reopen "<demo-path>" --port 8766 --mlflow-port 5001
```

The demo helper updates only its record's MLflow UI address to match the chosen
port. Normal dashboard browsing remains read-only.

## Plots

Default plot axes prefer metrics declared by the research records, with
unambiguous optimization directions, over incidental metrics such as runtime.
The two axes use distinct metrics when available. Unknown or conflicting
directions still require an explicit choice. Charts use larger point markers
and a wider plotting area, with horizontal scrolling on narrow screens.

- **Progress** plots one summary metric per run against timestamps, or a
  clearly labelled deterministic run index when timestamps are missing. A
  best-so-far overlay appears only when the metric direction is declared.
- **Pareto** plots two selected metrics and highlights the nondominated
  frontier. Directions use unambiguous declarations from the comparison
  scope; unknown or conflicting directions require an explicit choice.
- Missing and non-finite values are excluded from calculations with a visible
  omitted-row count and are never treated as zero.
- Filter the comparison cohort by a recorded parameter (for example an
  evaluation protocol or dataset split). Matching metric names alone do not
  make runs scientifically comparable.

The Refresh control fetches data explicitly; periodic refresh is off by
default. Record selection and scope changes reuse a source fetched in the
current refresh. Visual changes do not query tracking services. A failed
refresh retains the last successful data for the same record and scope,
labels it **STALE**, and displays the error. Run selection is preserved by
source and run ID when sorting or refreshing.

## Read-only guarantee

The bundled dashboard only reads records and tracking sources:

- it uses a non-mutating record index and never runs reparent recovery;
- browsing or selecting records never changes the CLI active record;
- sorting, chart switching, and point selection operate on the cached snapshot
  without re-querying the tracker;
- marimo session/cache files are kept in a temporary directory outside the
  research records.

Local MLflow SQLite databases are inspected through a temporary snapshot, so
MLflow initialization or schema migrations cannot change the original
database. Missing databases produce an unavailable-source error and are not
created. Other MLflow databases should be accessed through an HTTP tracking
server; direct non-SQLite database and legacy file-store URIs are not supported
by the dashboard's read-only path.

A custom notebook is trusted executable code. The read-only guarantee applies
to the bundled dashboard in its default mode; `--interactive` explicitly adds
the record creation and launch services described below.

## Interactive record creation

In interactive mode, **Create record** is available even when no record is
selected or no tracking source is configured. The form accepts an ID, parent
(**No parent (root)** is an explicit option), a free-string type, summary and
research fields, and expandable commit ref, related IDs, Markdown body, and
advanced local YAML controls.

- Preview is non-mutating; **Create** persists once through the public
  `Record.create(..., activate=False)` API and never changes the CLI active
  record.
- Advanced YAML is parsed with `yaml.safe_load`. `id`, `parent_id`,
  `related_to`, `markdown`, and `path` are rejected there; populated form
  fields cannot be overridden silently.
- Record IDs are project-wide unique. Child paths flatten to
  `hiraku/records/<root>/<child>.md`; inheritance and entrypoint merging
  follow the existing record rules. Empty local entrypoints/metrics/tracking
  fields are never written merely because a form field was left blank.
- Creation is blocked while a reparent recovery journal or malformed record
  exists. The dashboard never runs implicit recovery.

## Interactive launches

**Launch run** lists the selected record's effective entrypoints with their
defining record IDs. You choose the entrypoint, an installed dispatcher, and
optional argument overrides and commit-ref override.

- Overrides are a YAML mapping applied only to this launch. Booleans, numbers,
  null, and scalar sequences are preserved; a free-form shell command is never
  parsed.
- **Preview launch** renders the merged argv, dispatcher, record, requested
  commit and effective query source without starting anything. The requested
  commit is an input to the dispatcher, not a claim about what will execute:
  the local dispatcher reports the working tree's actual HEAD and clean/dirty
  state, and the worktree dispatcher requires a resolvable commit.
- Submit is explicit and pinned to the reviewed record/entrypoint. If the
  record or entrypoint changed after preview, the launch is rejected and you
  must preview again.
- Launch status and tracked-run status are separate. A successful process may
  log no run, and a failed process may log a partial run. The GUI states:
  **Your entrypoint logs results to the tracking backend.**
- Launch output is captured as temporary stdout/stderr log tails (64 KiB per
  stream). These are launch logs, separate from tracker artifacts. They are
  kept for at most the 20 most recent completed jobs and are deleted on
  shutdown.
- One launch runs at a time per dashboard server; duplicate events and
  concurrent submissions are rejected. Jobs are not queued and are not
  resumable across server restarts.
- The dashboard is a single-user local service. If the marimo server isolates
  browser sessions in separate processes, do not rely on one browser's
  controller state in another process; launch from the process that owns the
  server.
- There is no GUI cancellation button yet. Shutting down the server drains
  active workers, then terminates the owned process tree so dispatcher cleanup
  can run; it never kills only the worker and leaves training behind.
- After a job completes (success or failure), the dashboard requests exactly
  one fresh snapshot for the currently selected record/scope. A failed script
  may still have logged data. If no new run appears, refresh results and check
  whether the script logs nothing, the backend sync is delayed, or the query
  source is not the one the script wrote to.

## Custom notebook interface

A custom notebook receives project context through environment variables:

- `HIRAKU_PROJECT_ROOT` — absolute project root;
- `HIRAKU_DASHBOARD_RECORD` — initial record ID, empty when unset;
- `HIRAKU_DASHBOARD_SCOPE` — `record`, `subtree`, or `source`;
- `HIRAKU_DASHBOARD_INTERACTIVE` — `1` or `0`.

Use the snapshot API for backend-agnostic rendering:

```python
import marimo

app = marimo.App()


@app.cell
def _():
    import os
    from pathlib import Path

    import altair as alt
    import marimo as mo

    from hiraku.config import HirakuConfig
    from hiraku.dashboard import build_snapshot
    from hiraku.dashboard.plots import progress_points

    config = HirakuConfig.load(
        Path(os.environ["HIRAKU_PROJECT_ROOT"]) / "hiraku.yml"
    )
    snapshot = build_snapshot(
        config, record_id=os.environ.get("HIRAKU_DASHBOARD_RECORD") or None
    )
    progress = progress_points(snapshot.runs, "loss", direction="minimize")
    mo.stop(progress.frame.empty, mo.md("No loss values available."))
    mo.ui.altair_chart(
        alt.Chart(progress.frame).mark_point().encode(
            x=alt.X("value:Q", title="Loss"), y="run_id:N"
        )
    )
    return


if __name__ == "__main__":
    app.run()
```

Snapshot version 2 exposes `records` (effective fields and defining record IDs),
`runs` (source-qualified keys, provenance, metrics, parameters, and adapter-owned
URLs), `source`, `sources` (queried-source coverage), `scope`, `errors`, and
`refreshed_at`. These are plain Python structures.
Use `DashboardSession.refresh()` for coalescing and last-successful-data handling.
Keep UI creation, snapshot fetching, and visual transformations in separate
marimo cells so changes to visual controls do not trigger fetching.

## Troubleshooting

- **`No hiraku.yml found`** — run the command from inside a Hiraku project or
  initialize one first.
- **`The dashboard requires the optional 'dashboard' dependencies`** — run
  `uv sync --extra dashboard`.
- **`Port must be an integer between 1 and 65535`** — choose another `--port`.
- **`Backend unavailable`** — the tracking source could not be read. Fix the
  backend configuration or storage, then press *Refresh*.

## Development checks

To exercise the dashboard with real model training and local MLflow tracking,
run the test script from the repository:

```bash
uv sync --all-extras
uv run --all-extras python scripts/test_dashboard.py
```

It creates a temporary Git/Hiraku project from the bundled Digits example,
trains the weak- and strong-L2 experiments for 40 epochs, verifies their
tracked metrics and plot data, and opens both the dashboard (port 8765) and
MLflow UI (port 5000). Use `--port` and `--mlflow-port` to change the ports;
automated tests are skipped by default. Add `--run-tests` to run regression
tests and the notebook check before starting the demo.
Ctrl-C stops both servers. The Digits demo opens on the `digits` record in
**This subtree** scope, so both child experiments are visible and the record
graph explains the hierarchy. Its plot defaults are `val/log_loss` and
`val/accuracy`, with inherited optimization directions. Run links use MLflow's
native run route (`/#/experiments/<id>/runs/<run_id>`) and are built from the
configured `ui_url`, which the demo derives from `--mlflow-port`. The printed
MLflow link still opens the **Model training** view of `hiraku-digits` directly.
Dashboard plots show final metrics per run; full epoch histories remain in MLflow.
See `scripts/README-dashboard.md` for the walkthrough, repeat-run commands,
and the optional `--example csv` fixture.

Run the notebook execution, query, launcher, and plot regressions with:

```bash
uv run --all-extras pytest tests/dashboard
uv run --all-extras marimo check src/hiraku/dashboard/app.py
```

Browser acceptance uses Selenium from the development dependency group and
a locally installed browser. It is opt-in for ordinary unit-test runs. With
Chrome installed, set `HIRAKU_TEST_BROWSER=chrome` and run:

```bash
uv run --all-extras pytest tests/dashboard/test_dashboard_browser.py -n 0
```

On Linux, WebKitGTK is also supported: install the distribution's
`webkitgtk-webdriver` (called `webkit2gtk-driver` on older distributions)
and `xvfb` packages, set `HIRAKU_TEST_BROWSER=webkit`, and run the same
command under `xvfb-run -a`. These checks create temporary fixture projects;
they require no tracking credentials.

## Implementation review validation

The review began at `280e33b`. The checked-out tracking API uses
`backend_for()` and `list_all_recorded_runs()`; the dashboard resolves fields
against its non-mutating index before choosing that adapter. PR 77's remote
status could not be rechecked because GitHub authentication failed. No
upstream branch was merged or cherry-picked.

| Plan tickets | Validation |
| --- | --- |
| HD-00/01 | Optional imports, Python 3.11–3.14 suites, marimo notebook execution and static checks |
| HD-02 | Source caching, inherited configuration, storage identity, malformed metadata, journals, bounded rereads, deep trees, and unchanged-file assertions |
| HD-03 | Launcher forwarding, dependency errors, occupied ports, interruption cleanup, isolated core/dashboard wheel installs |
| HD-04/05 | Browser record/run selection, provenance, source scope, sorting, progress-point selection and Pareto switching; pure plot regressions |
| HD-06 | Coalesced refresh, stale snapshots, explicit browser refresh, source/run selection retention and custom-notebook forwarding |
| HD-07 | Local CSV/MLflow and mocked W&B coverage, distribution smoke checks, browser workflow and scale measurement |

The browser checks used WebKitGTK on a Linux ARM64 container with eight
reported logical CPUs, Python 3.14.4, uv 0.9.26, marimo 0.25.1, Altair 6.3.0
and pandas 2.3.3. The synthetic 1,000-record/10,000-run case took 0.20 seconds
to fetch, 3.75 seconds to load the notebook and chart in the browser, and
0.74 seconds to sort and select a run. Browser load includes notebook
execution and rendering; these measurements are not latency guarantees.
The tracker call-count regression verifies one source query per refresh.

The full suite passed on each Python version from 3.11 through 3.14:
145 passed and four skipped. The skips are the opt-in browser workflow and
three existing external-integration tests. The browser workflow also passed
explicitly against both the checkout and the installed wheel. Ruff lint and
format checks, the changelog check, documentation build, and package build passed.
Chrome and macOS browser checks were not run in this environment. Direct
non-SQLite MLflow database access remains outside the read-only dashboard
path; use a tracking server for those stores.
