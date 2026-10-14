# Dashboard

`hiraku dashboard` serves a local page for browsing records and the runs
attributed to them. The page is a [marimo](https://marimo.io) notebook: you can
use it as it is, read its code, or copy it into your project and change it.

Install the optional dependencies first:

```bash
uv sync --extra dashboard              # from a Hiraku checkout
uv tool install "hiraku[dashboard]"    # or for an installed CLI
```

## Modes

Run every command from inside a Hiraku project.

| Command | Mode | What it does |
| --- | --- | --- |
| `hiraku dashboard` | View | Serves the read-only dashboard. |
| `hiraku dashboard --include-code` | View with code | Same page; marimo's menu can also show each cell's code. |
| `hiraku dashboard --init` | Customize | Copies the notebook to `hiraku/dashboard/app.py` and exits. |
| `hiraku dashboard --edit` | Edit | Opens that copy in the marimo editor. |
| `hiraku dashboard --init --edit` | Customize and edit | Both in one step. |

### View

```bash
hiraku dashboard --record digits
```

The page is served at <http://127.0.0.1:8765> and opens in your browser.
`--port` changes the port, `--no-browser` only prints the URL, and `--record`
selects a record when the page opens (default: the active record). Press
Ctrl-C to stop the server. Viewing never writes to the project.

Add `--include-code` to send the notebook's code to the page. Open marimo's
menu (**⋯**, top right) and choose to show the code to see each cell's source
above its output. The code is read-only here; use the edit mode to change it.

### Customize

```bash
hiraku dashboard --init
```

Copies the bundled notebook to `hiraku/dashboard/app.py` with a `.gitignore`
for marimo's `__marimo__/` caches. From then on, `hiraku dashboard` serves this
copy instead of the bundled notebook, so commit it with the project. `--init`
refuses to overwrite an existing copy; delete `hiraku/dashboard/` to start
over from the bundled version.

The copy imports its data and styling helpers from the installed
`hiraku.dashboard` package, so it stays short and picks up fixes to them.

### Edit

```bash
hiraku dashboard --edit
```

Opens the project's copy in the marimo editor, which reloads when the file
changes on disk, for example when an agent edits it. `--edit` requires a copy;
run `--init` first, or both at once with `hiraku dashboard --init --edit`.
Saved changes appear the next time you run `hiraku dashboard`.

The editor runs arbitrary code, so, unlike the read-only page, it is protected
by marimo's access token, which is part of the URL it prints and opens. You can
also open the copy without Hiraku's CLI, with
`marimo edit hiraku/dashboard/app.py`; it then finds its project from its own
location.

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

## Run and record tables

The notebook defines two pandas DataFrames backed by Apache Arrow, which you
can use in the edit mode:

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

For example, this cell compares two metrics across all runs, coloured by
record. Vega-Lite reads `.` in a field name as nesting, so escape it:

```python
@app.cell
def _(alt, runs_df):
    alt.Chart(runs_df).mark_point().encode(
        x=alt.X(r"metric\.val/log_loss:Q", title="val/log_loss"),
        y=alt.Y(r"metric\.val/accuracy:Q", title="val/accuracy"),
        color="record:N",
        tooltip=["run", "record"],
    )
    return
```

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
