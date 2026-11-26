# Dashboard

The Hiraku dashboard lets you browse project records, inspect their runs, and
compare metrics in your browser. It runs locally as a [marimo](https://marimo.io)
notebook with a [Marimo Studio](https://marimo-team.github.io/marimo-studio/) view.

Install Hiraku with the dashboard extra:

```bash
uv tool install --force 'hiraku[dashboard]'
```

Add any tracking extras you use to the same install command. For example, use
`uv tool install --force 'hiraku[dashboard,mlflow]'` for MLflow.
See [installation options](../quickstart/installation.md).

## Browse your project

To open the dashboard, run this from inside your Hiraku project:

```bash
hiraku dashboard serve
```

Hiraku opens the dashboard in your browser and prints its local URL in the
terminal. For projects without a dashboard of their own, it opens the included
dashboard without writing project files. Press Ctrl-C to stop the server.

In the leaderboard's **Table** view, use **Columns** to show more metrics,
parameters, or run provenance.

| Option | What it does |
| --- | --- |
| `--record ID`, `--leaderboard NAME` | Select the initial record or leaderboard. |
| `--port PORT` | Use a specific port. By default, Marimo picks a free port. |
| `--no-browser` | Print the local URL without opening a browser. |
| `--watch` | Reload when the notebook file changes. |
| `--include-code` | Make read-only cell source available from Marimo’s menu. |

## Project dashboards

Copy the included dashboard into your project to customize the marimo notebook, layout and plots.

```bash
hiraku dashboard create overview
```

Edit the cells in `hiraku/dashboards/overview.py` directly or open the notebook
editor with `hiraku dashboard edit overview`. The Studio layout and styles
live in `hiraku/dashboards/studio/overview/dashboard/`.

To open your copy in the browser, run:

```bash
hiraku dashboard serve overview
```

If the project has only one dashboard, you can omit its name. Use
`hiraku dashboard list` to see the available names.

For a plain Marimo starter, run
`hiraku dashboard create exploration --template notebook`. Edit and serve
`hiraku/dashboards/exploration.py` the same way. The notebook exposes `project`,
`records`, `leaderboard_names`, the selected `leaderboard`, and its `runs` to
other cells. It loads the sole leaderboard automatically, or you can use
the picker or `--leaderboard NAME` to select one.
