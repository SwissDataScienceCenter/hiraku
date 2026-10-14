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
