# Dashboard

`hiraku dashboard` opens a local, read-only page for browsing records and the
runs attributed to them. It never writes to the project.

Install the optional dependencies first:

```bash
uv sync --extra dashboard        # from a Hiraku checkout
uv tool install "hiraku[dashboard]"   # or for an installed CLI
```

Then, from inside a Hiraku project:

```bash
hiraku dashboard --record digits
```

The page is served at <http://127.0.0.1:8765> (`--port` changes it) and opens
in your browser unless you pass `--no-browser`. Without `--record`, the active
record is selected. Press Ctrl-C to stop the server.

## Sections

**Runs**
:   Records and their runs in one indented table. Runs appear under the record
    they are attributed to, in execution order, with the value of the first
    metric the records declare. Select a record to compare its subtree, or a
    run to inspect it.

**Run**
:   The selected run's ID, record, entrypoint, commit, start time, parameters,
    and metrics, plus a link to the run in its tracking backend when one is
    available.

**Performance**
:   Every run under the selected record, numbered 1 to N by start time on the
    x axis, with a metric you choose on the y axis. Colours mark the record and
    a ring marks the selected run. Selecting a run inside the current subtree
    keeps that comparison; selecting one outside switches to the run's record.

Use **Refresh** to re-read records and tracking sources after new launches.

## See and change the notebook

The dashboard is a [marimo](https://marimo.io) notebook. Pass `--include-code`
to let the read-only page show each cell's code from marimo's menu.

To customize the dashboard, copy the notebook into the project and open it in
the marimo editor:

```bash
hiraku dashboard --init          # creates hiraku/dashboard/app.py
hiraku dashboard --edit          # edits that copy
hiraku dashboard --init --edit   # both at once
```

Once the copy exists, `hiraku dashboard` serves it instead of the bundled
notebook. Commit it with the project; marimo's caches in
`hiraku/dashboard/__marimo__/` are ignored. `--init` refuses to overwrite an
existing copy, and `--edit` requires one. The editor runs arbitrary code, so
unlike the read-only page it is protected by marimo's access token, which is
part of the URL it prints and opens.

The copy imports its data and styling helpers from the installed
`hiraku.dashboard` package. You can also open it directly with
`marimo edit hiraku/dashboard/app.py`; it then finds its project from its own
location.

The notebook also defines two pandas DataFrames backed by Apache Arrow:

`runs_df`
:   One row per run in execution order: `run`, `record`, `record_path`,
    `source`, `started`, `entrypoint`, `commit`, `url`, plus a
    `metric.<name>` and `param.<name>` column for each metric and parameter.

`records_df`
:   One row per record in tree order: `record`, `parent`, `record_path`,
    `type`, `status`, `decision`, `metrics` (declared names and directions),
    and `description` (the record's Markdown).

`record_path` is the record's ancestry, such as `digits/weak-l2`, so
`runs_df[runs_df.record_path.str.startswith("digits/")]` selects a subtree.
Use these tables in your own cells, or mention `@runs_df` and `@records_df` in
marimo's AI assistant to ask questions about your runs. MLflow stores
parameters as text, so cast them before comparing numbers, for example
`runs_df["param.alpha"].astype(float)`.

## Links to tracking backends

Run links come from the tracking backend. For MLflow, set `ui_url` when you
track to a local database, or use an HTTP(S) `tracking_uri`; see
[MLflow](tracking-mlflow.md). Without a link, the run details are still shown.

## Try it on the Digits example

From a Hiraku checkout, this script creates a Digits project with three
regularization records, trains each with three seeds, and serves both the
MLflow UI and the dashboard:

```bash
uv run --all-extras python scripts/dashboard_demo.py
```

Use `--epochs` to change the training budget (default 10), `--dir` to choose
the project directory, and `--no-serve` to stop after the nine runs.
