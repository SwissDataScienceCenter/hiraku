# Dashboard

`hiraku dashboard serve` serves a local page for browsing records and the runs
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
| `hiraku dashboard serve` | View | Serves the read-only dashboard. |
| `hiraku dashboard serve --watch` | Watch | Reloads the notebook when it changes on disk. |
| `hiraku dashboard serve --include-code` | View with code | Same page; marimo's menu can also show each cell's code. |
| `hiraku dashboard create NAME` | Customize | Creates `hiraku/dashboards/NAME.py` and its Studio view. |
| `hiraku dashboard list` | Discover | Lists project dashboard names. |
| `hiraku dashboard serve NAME` | View | Serves the named notebook. |
| `hiraku dashboard edit NAME` | Edit | Opens the notebook in marimo-studio. |

With no project dashboards, `hiraku dashboard serve` serves the bundled
dashboard without writing project files. With one project dashboard, it serves
that dashboard. With several, use `hiraku dashboard serve NAME`.
Use `hiraku dashboard edit` without a name to edit the sole project dashboard.

### View

```bash
hiraku dashboard serve --record digit-classification
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
`hiraku dashboard serve` serves this copy when it is the sole dashboard, so commit
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
Saved changes appear the next time you run `hiraku dashboard serve`.

The notebook editor runs arbitrary code, so, unlike the dashboard, it is protected
by marimo's access token, which is part of the URL it prints and opens. You can
also open the copy without Hiraku's CLI, with
`marimo edit hiraku/dashboards/overview.py`; it then finds its project from its own
location.

## Leaderboards

Choose a leaderboard using the selector, or open one with
`hiraku dashboard serve --leaderboard NAME`. A sole leaderboard is selected automatically.
Plot and Table show all attributed runs by default, including runs whose record
files are missing. Use the **Filter** chips below the Leaderboard title to choose
runs attributed to the **Selected record** and its descendants.
**Include unattributed** adds runs without a record to any of these views.
The **Selected record** chip requires a selection. Clearing the selection turns
it off. Metric names and directions come from the leaderboard, and
parameters are discovered automatically.

## Sections

**Leaderboard**
:   Choose a leaderboard in either view. The axis selectors sit below the chart
    in **Plot**. The **X axis** defaults to **Run order**. Choose a **Y axis**
    metric to see performance in execution order. The plot highlights runs
    attributed to the selected record and enlarges the selected run's point.
    Click a point to select the run and open its details in the record below.
    Choose a metric on both axes to compare the filtered runs in a scatter plot.
    Axis labels show the configured metric directions and default to minimizing.
    **Table** shows the filtered runs with their
    start time, duration, and first leaderboard metric. Enable **Show records**
    to interleave records with their runs. Select a run row to inspect the run
    and select its owning record.

**Record navigation**
:   Choose the **Files** (default) or **Lineage** tab in the left sidebar.
    Each record row shows its title and type on one line. Untitled records use
    the filename stem. The Record card header shows the title and full
    bundle-relative ID with a **Copy** button.
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
    Directories follow bundle-relative record paths, independently of lineage.
    Expand or collapse directories, and toggle type and state filter chips. Values within
    one filter are combined with OR; the two filters are combined with AND.
    Filtering navigation does not change the selected record or run scope.
    Choosing a record selects its table row when records are included, clears
    any selected run, and sets the target for the Leaderboard record filters.
    Table selections highlight the corresponding record in the tree.

**Selected record**
:   A compact summary of local metadata, expandable runs and raw front matter,
    followed by its Markdown body. Commit and branch links are shown when the
    project's actual Git origin points to GitHub. Run links use the tracking
    backend's HTTP(S) URL when available; missing links stay plain text.
    Tracking links appear beside the run IDs under **Runs**. Expand a run to
    see its commit, start time, duration, parameters, and metrics.
    Selecting a plot point or run row
    opens its disclosure. If the run has no available record, its details
    appear directly below the Leaderboard.
    Selecting a run shows its owning record and targets it with the active
    Leaderboard record filters.

Use **Refresh** to re-read records and tracking sources after new launches.

### Reusing plots

`hc.performance_plot(runs, y_metric="loss")` plots loss over execution order.
Pass `x_metric="accuracy"` to compare the two metrics in a scatter plot.
The component accepts an already scoped run table and caller-owned selection.

Place the Leaderboard header and both views together in Studio. The header
includes the filter chips and run count. The active tab controls which view
renders:

```html
<marimo-cell name="leaderboard_header"></marimo-cell>
<marimo-cell name="leaderboard_plot"></marimo-cell>
<marimo-cell name="leaderboard_table"></marimo-cell>
```

### Reusing record navigation

The bundled notebook initializes independent Marimo state values together in
`data_setup`. View cells render controls from those values and pass callbacks
back to the relevant setter. The record header displays the Files/Lineage
tabs and filter chips. Both views use the same record selection and filters.

`hc.filesystem_tree(records, selected_id, on_select, types=(), states=())`
renders filesystem navigation, retaining directories containing matching records.
Directory nodes only expand and collapse. The bundled notebook supplies type
and state toggle chips using `hc.record_filters(records, on_toggle, types=(),
states=())`, which returns `(filters, buttons)` with caller-owned filter state.
Keep the returned buttons alive. Navigation shares record selection with the run
table. On narrow screens the sidebar stacks above the main content.

`hc.lineage_tree(records, selected_id, on_select, types=(), states=())` renders
lineage navigation using `parent_id` and retains ancestors of matching records.
Both components return `(tree, buttons)` and
load no data or own selection state.

Keep `buttons` in a notebook variable so their callbacks remain live. Wire
`on_select` into the same selection callback used by the run table; the bundled
notebook uses `select_record(record_id)` and passes the resulting key to
`hc.run_table(..., selected_key=key, on_select=select_key)`.

The Studio entry document places `record_header`, `record_files`, and
`record_tree` in the sidebar's
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
```

Loading and persistence remain with the caller. The component does not load,
parse, or save files, and rendering and selection do not change record objects
or Markdown files. Passing `None` displays the empty selection state.

## Run and record tables

The notebook reads your project once and builds two pandas DataFrames backed
by Apache Arrow for the run table, run details, and metric analysis. Record
details use the already loaded record objects to retain complete front matter.

`hd.run_rows(runs, records=records, source=leaderboard.source.type)` converts
queried runs to rows. Tracking errors leave record navigation available.

`runs_df`
:   One row per attributed run in execution order: `run`, `record`, `record_path`,
    `source`, `started`, `commit`, `url`, plus a
    `metric.<name>` and `param.<name>` column for each metric and parameter.

`all_runs_df`
:   The same columns, including runs with no record attribution.

`records_df`
:   One row per record in tree order: `record`, `parent`, `record_path`,
    `type`, `state`, `decision`, `commit`, and `description` (the record's Markdown).

| View | Reads |
| --- | --- |
| Leaderboard table | filtered `leaderboard_runs`, with optional rows from `records_df` |
| Run details | the selected run's row of `all_runs_df` |
| Performance plot | filtered `leaderboard_runs`; choose one metric |
| Trade-off plot | filtered `leaderboard_runs`; choose two metrics |

`record_path` is the record's display ancestry, such as `digits/weak-l2`.
Use these tables in your own cells, or mention `@runs_df` and
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
hiraku dashboard serve --record digit-classification
```

The dashboard reads the six bundled runs through the MLflow server.
You can also try the other modes in that project:

```bash
hiraku dashboard serve --include-code --record digit-classification
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
