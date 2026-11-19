# Dashboard

The dashboard lets you browse research records, inspect individual runs, and plot metrics from a leaderboard. Install the `dashboard` extra using the [installation instructions](../installation.md#optional-features).

## Browse results

From a Hiraku project, run:

```bash
hiraku dashboard
```

Select a record to compare runs in its subtree, or select a run to see its metrics, parameters, and source link when available. Use the pen icon in the record panel to edit and save its Markdown body.

Use `--record ID` to open on a specific record, or `--leaderboard NAME` to pick a source. The dashboard opens in your browser and prints its local URL. Use `--no-browser` to print the URL only, `--port` to set the port, and Ctrl-C to stop the server.

## Customize the layout

```bash
hiraku dashboard --init
hiraku dashboard --edit
```

The first command copies the bundled notebook and Studio view into `hiraku/dashboard/`. The second opens the copy in Marimo Studio. Subsequent calls to `hiraku dashboard` serve that copy.

Edit `app.py` for notebook cells and `studio/dashboard/index.html` for the layout. The HTML references named notebook cells and can use local CSS and JavaScript. Embed images inline.
