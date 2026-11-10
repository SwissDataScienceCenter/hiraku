# Dashboard

`hiraku dashboard` serves a local page for browsing records and the runs
attributed to them. The page is a [marimo](https://marimo.io) notebook served
through [Marimo Studio](https://marimo-team.github.io/marimo-studio/). Its
performance and metric trade-off plots are separate, independently placeable cells.

Install the optional dashboard dependencies first:

```bash
uv tool install --force 'hiraku[dashboard]'
```

Include all extras you want to use, for example, when using MLflow or W&B
`uv tool install --force 'hiraku[dashboard,mlflow]'`. See [installation options](../quickstart/installation.md).

## Modes

Run every command from inside a Hiraku project.

| Command | Mode | What it does |
| --- | --- | --- |
| `hiraku dashboard` | View | Serves the dashboard with record Markdown editing. |
| `hiraku dashboard --watch` | Watch | Reloads the notebook when it changes on disk. |
| `hiraku dashboard --include-code` | View with code | Same page; marimo's menu can also show each cell's code. |
| `hiraku dashboard create NAME` | Customize | Creates `hiraku/dashboards/NAME.py` and its Studio view. |
| `hiraku dashboard list` | Discover | Lists project dashboard names. |
| `hiraku dashboard serve NAME` | View | Serves the named notebook. |
| `hiraku dashboard edit NAME` | Edit | Opens the notebook in marimo-studio. |

Without a name, `hiraku dashboard serve` serves the sole project dashboard.
With several dashboards, it asks you to select a name. With none, it serves the
bundled dashboard without writing project files. `hiraku dashboard` is shorthand
for this default behavior. Existing `hiraku/dashboard/app.py` copies remain
available as `overview`; a new `hiraku/dashboards/overview.py` takes precedence.
The older `--init` flag creates `overview`. Editing uses `hiraku dashboard edit`;
the `--edit` flag is no longer supported. Omit the name to edit the sole dashboard.

### View

```bash
hiraku dashboard --record digit-classification
```

The page is served on localhost and opens in your browser. Hiraku selects a
free port automatically, starting at 2718, and prints the URL in the terminal.
Use `--port` to request a specific port, `--no-browser` to print the URL without
opening it, and `--record` to select a record when the page opens. Press Ctrl-C
to stop the server. Viewing never writes to the project.

Add `--include-code` to send the notebook's code to the page. Open marimo's
menu (**⋯**, top right) and choose to show the code to see each cell's source
above its output. The code is read-only here; use the edit mode to change it.

### Customize

```bash
hiraku dashboard create overview
```

Choose between two bundled templates:

| Template | Contents |
| --- | --- |
| `dashboard` (default) | Record/run notebook with an HTML Studio view. |
| `notebook` | Minimal Marimo notebook with Markdown and an analysis cell; no Studio view. |

```bash
hiraku dashboard create exploration --template notebook
hiraku dashboard edit exploration
```

The notebook template needs the dashboard Python dependencies but no Quarto or
frontend build tools. Add Markdown cells, data loading, widgets, and plots directly
to `hiraku/dashboards/exploration.py`.

The default template copies the notebook to `hiraku/dashboards/overview.py` and the Studio view,
including its HTML, CSS, and logos, to `hiraku/dashboards/studio/overview/`, with a
`.gitignore` for marimo's `__marimo__/` caches and Studio's build state. From then on,
`hiraku dashboard` serves this copy instead of the bundled notebook, so commit
it with the project. Creation refuses to overwrite an existing notebook or view
directory. Each notebook has independent views under `studio/<name>/`.
You can also add your own top-level `.py` Marimo notebooks to `hiraku/dashboards/`;
Studio views are optional for notebooks that display their content directly.

The notebook imports data helpers from `hiraku.dashboard.data` and reusable
widgets and renderers from `hiraku.dashboard.components`. Components build the
record/run table, run details, metric selectors, and metric-analysis output
from the loaded tables; they do not query tracking backends. Notebook cells
own data loading and shared selection, and read widget values explicitly so
marimo can rerun dependent cells when a selection or metric changes.

Imports live in `app.setup`. Named cells separate project discovery
(`data_setup`), refresh-triggered loading (`project_data`), selection state,
and rendering. The Studio view references the output cells by name; data and
state cells do not need HTML hosts.

Branding and page layout live in `studio/overview/dashboard/index.html` and its CSS and
assets; edit those files to customize the presentation. To customize a widget
or plot, replace its component call in the notebook cell. Project copies pick
up component fixes from the installed Hiraku package.


### Edit

```bash
hiraku dashboard edit overview
```

Opens the project's copy in marimo-studio, with the notebook beside a live
preview of the dashboard. It reloads when the file changes on disk, for
example when an agent edits it. The view's `index.html` places the notebook's
named cells on the page; add a named cell there to show it on the dashboard. Editing requires a copy;
run `hiraku dashboard create overview` first.
Saved changes appear the next time you run `hiraku dashboard`.

The notebook editor runs arbitrary code, so, unlike the dashboard, it is protected
by marimo's access token, which is part of the URL it prints and opens. You can
also open the copy without Hiraku's CLI, with
`marimo edit hiraku/dashboards/overview.py`; it then finds its project from its own
location.

## Leaderboards

Choose a leaderboard using the selector, or open one with
`hiraku dashboard --leaderboard NAME`. A sole leaderboard is selected automatically.
Plots show attributed runs by default, including runs whose record files are
missing. Enable **Include unattributed runs** to show the entire source.
Without an initial `--record`, plots span the selected leaderboard; select
**Clear selection** in the runs table to clear a subtree filter. Metric names and directions
come from the leaderboard; parameters are discovered automatically.

## Sections

**Record navigation**
:   Choose the **Files** (default) or **Lineage** tab in the left sidebar.
    Records show their title above a smaller bundle-relative ID, with type and
    state badges. Untitled records use the filename stem as their title.
    The view tabs sit beside the Records heading, aligned right. Files and Lineage share
    row styling and indentation; folder names use disclosure chevrons without
    icons or trailing slashes.
    Drag the divider to adjust sidebar width. With the divider focused, use
    Left/Right arrow keys to resize, Home/End for the limits, or double-click
    to reset. Width lasts for the current page; narrow screens remain stacked.
    Files shows filesystem paths; Lineage follows declared `parent_id` links.
    Switching views preserves record selection and run scope. Both share the
    Type/State filter chips; lineage retains ancestors of matches, dimming
    ancestors that do not themselves match. Missing parents and cycles are tolerated.
    The filesystem tree has type and state badges.
    Directories follow bundle-relative record paths, independently of lineage.
    Expand or collapse directories, and toggle type and state filter chips. Values within
    one filter are combined with OR; the two filters are combined with AND.
    Filtering navigation does not change the selected record or run scope.
    Choosing a record
    selects the same row in the runs table, clears any selected run, and compares
    that record's subtree. Table selections highlight the corresponding record
    in the tree; selecting a run highlights its owning record while preserving
    the current comparison subtree when the run lies within it.

**Runs**
:   Records and their runs in one indented table. Runs appear under the record
    they are attributed to, in execution order, with their start time, duration,
    and the value of the first metric the leaderboard declares or discovers. Select a record to
    compare its subtree, or a run to inspect it.

**Run**
:   The selected run's ID, record, commit, start time, duration,
    parameters, and metrics, plus a link to the run in its tracking backend when
    one is available.

**Record**
:   Expandable YAML front matter labeled with the selected record's ID,
    followed by its Markdown body.
    Click the pen to edit the body, then **Save** to write it back or **Cancel**
    to discard the draft. Front matter stays read-only. Drafts remain available
    when switching between records in the same session. Save errors keep the
    draft open; if the file changed externally, copy your draft before canceling
    and refreshing to load the latest version.
    Selecting a run shows its owning record, while the metric analysis keeps
    the selected comparison subtree when that run lies within it.

**Performance over runs**
:   Shows runs in the selected record subtree
    in execution order, with a metric on the y axis. Colours mark the record
    and a ring marks the selected run. Choose its y-axis metric independently
    of the trade-off plot.

**Metric trade-offs**
:   Compares two metrics in the selected subtree; set whether each should be minimized or
    maximized. Diamonds mark runs for which no comparable run matches or
    improves both metrics while improving at least one. Runs without finite
    numeric values for both metrics are excluded.

Use **Refresh** to re-read records and tracking sources after new launches.

### Reusing plots

`hc.performance_plot(runs, scope=scope, metric="loss", selected_run=selected_run)`
plots one metric over execution order. `hc.tradeoff_plot(runs, x_metric="loss",
y_metric="accuracy", x_objective="minimize", y_objective="maximize")` compares
two metrics and lists non-dominated runs. Both accept an already scoped run table
and create no controls or selection state.

Use `hc.performance_controls(metrics, default_metric)` for the first plot's
metric selector and `hc.tradeoff_controls(directions, metrics)` for the second
plot's four selectors. Keep the returned widgets in notebook variables and pass
their values to the corresponding plot.

The bundled notebook displays both plots without a mode selector. Each has its
own named cell and can be placed, moved, or omitted independently in Studio:

```html
<marimo-cell name="performance_plot"></marimo-cell>
<marimo-cell name="tradeoff_plot"></marimo-cell>
```

### Reusing record navigation

Use `hc.RecordNavigation` to compose the heading, Files/Lineage tabs, filter
chips, and active tree. Create it once in a setup cell, connecting its change
callback to Marimo state:

```python
get_revision, set_revision = mo.state(0, allow_self_loops=True)
navigation = hc.RecordNavigation(
    on_change=lambda: set_revision(lambda revision: revision + 1)
)
```

In a separate display cell, read the revision and render:

```python
get_revision()
navigation.render(records, selected_id=selected_id, on_select=on_select)
```

The component retains view/filter state and widget references across selection
changes. The caller owns record selection and loading. The independent filter
and tree renderers remain available for custom layouts.

`hc.filesystem_tree(records, selected_id, on_select, types=(), states=())`
renders filesystem navigation, retaining directories containing matching records.
Directory nodes only expand and collapse. The bundled notebook supplies type
and state toggle chips using `hc.record_filters(records, on_toggle, types=(),
states=())`, which returns `(filters, buttons)` with caller-owned filter state.
Keep the returned buttons alive. Navigation shares record selection with the run
table. On narrow screens the sidebar stacks above the main content.

`hc.record_tree(records, selected_id, on_select)` remains available for lineage
navigation using `parent_id`. Both components return `(tree, buttons)` and
load no data or own selection state.

Keep `buttons` in a notebook variable so their callbacks remain live. Wire
`on_select` into the same selection callback used by the run table; the bundled
notebook uses `select_key("record:" + record_id)` and passes the resulting key to
`hc.run_table(..., selected_key=key, on_select=select_key)`.

The Studio entry document places the `record_navigation` cell in the sidebar's
`<aside>` element. Remove that element from a project copy to omit navigation;
table selection and the remaining components continue to work independently.

Reusable component styling lives in `studio/overview/dashboard/components.css` beside the
Studio view's other stylesheets. The notebook loads it through
`marimo.App(css_file="studio/overview/dashboard/components.css")` for notebook mode.
The Studio HTML links the same file with `crossorigin="anonymous"`, allowing
Marimo to read and adopt its rules into widget shadow roots in the isolated
presentation. No styles are injected into button labels. `--init` copies the
stylesheet with the view for customization. Shared tree hierarchy, row selection,
filter chips, and navigation-tab styles live in this stylesheet. The split-page
layout remains in the Studio view's `style.css`.

### Reusing Markdown rendering

`hiraku.dashboard.components.markdown_view(record)` accepts a `Record`. It reads
the ID, front matter, and Markdown body directly from the object, displaying
collapsed metadata labeled with the ID above the body. For example:

```python
hc.markdown_view(record)
hc.markdown_view(record)
```

Loading and persistence remain with the caller. The component does not load,
parse, or save files, and rendering and selection do not change record objects
or Markdown files. Passing `None` displays the empty selection state.

For editing, create `hc.MarkdownEditor(on_save=Record.save_markdown, on_change=...)` once
in a notebook cell and call `hc.markdown_view(record, editor=editor)` in a
separate display cell. Omitting `editor` keeps the viewer read-only.
Wire `on_change` to a `mo.state` setter with `allow_self_loops=True`, and read
its getter in the display cell so button actions rerender it. The
`Record.save_markdown` method saves through the record's own API and rejects
changes made on disk since editing began. The editor changes
the displayed object's body only after a successful save. Detached records
remain read-only until saved to a path. Drafts are held in memory, so reloading
or closing the session discards them.

## Run and record tables

The notebook reads your project once and builds two pandas DataFrames backed
by Apache Arrow for the run table, run details, and metric analysis. Record
details use the already loaded record objects to retain complete front matter.

`hd.run_rows(runs, records=records, source=leaderboard.source.type)` converts
queried runs to rows. Tracking errors leave record navigation available.

`runs_df`
:   One row per run in execution order: `run`, `record`, `record_path`,
    `source`, `started`, `commit`, `url`, plus a
    `metric.<name>` and `param.<name>` column for each metric and parameter.

`records_df`
:   One row per record in tree order: `record`, `parent`, `record_path`,
    `type`, `state`, `decision`, `commit`, and `description` (the record's Markdown).

| View | Reads |
| --- | --- |
| Runs table | `records_df` rows, each followed by its runs from `runs_df` |
| Run details | the selected run's row of `runs_df` |
| Performance plot | selected record subtree in `runs_df`; choose one metric |
| Trade-off plot | selected record subtree in `runs_df`; choose two metrics and their objectives |

`record_path` is the record's ancestry, such as `digits/weak-l2`.
`subtree(runs_df, "digits")` selects every run of `digits` and the records
below it. Use these tables in your own cells, or mention `@runs_df` and
`@records_df` in marimo's AI assistant to ask questions about your runs.
Values keep the types the tracking backend reports; some backends, such as
MLflow, report parameters as text, so cast them before comparing numbers, for
example `runs_df["param.alpha"].astype(float)`.

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

## Tracking backends

Runs, metrics, parameters, and links come from the selected leaderboard's
CSV, MLflow, or W&B source, queried once per refresh.

A run's link label names its backend, for example **Open in mlflow**. Links
appear when the backend provides an HTTP(S) URL for the run. For MLflow, set
`ui_url` when its UI is served separately from the tracking server;
see [MLflow](tracking-mlflow.md). Without a link, the run details are still
shown.

## Try it on the Digits example

The bundled Digits example includes three records and six measured MLflow runs,
so you can try the dashboard without training first. Install both optional
extras, then create a project with the prepared results (Git is optional):

```bash
uv tool install --force 'hiraku[dashboard,mlflow]'
hiraku example create digits digits-dashboard --with-results
cd digits-dashboard
uv sync
uv run mlflow server --backend-store-uri sqlite:///mlflow.db \
  --host 127.0.0.1 --port 5000
```

Keep the server running. In another terminal in the project directory:

```bash
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
hiraku dashboard --record digit-classification
```

The dashboard reads the six bundled runs through the MLflow server.
You can also try the other modes in that project:

```bash
hiraku dashboard --include-code --record digit-classification
hiraku dashboard create overview
hiraku dashboard edit overview --record digit-classification
```

Run details include an **Open in mlflow** link to the server UI. To persist the
server address, set `tracking_uri` in `hiraku/leaderboards/default.yml`:

```yaml
source:
  type: mlflow
  tracking_uri: http://127.0.0.1:5000
  experiment_name: hiraku-digits
```

See the [Digits example](../examples/digits/README.md) to inspect the runs in
MLflow or generate fresh results.
