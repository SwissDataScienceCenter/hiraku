# Interactive dashboard manual

This manual covers the GUI opened by the
[use-case demo helper](../examples/index.md). The same
workflow applies to your own initialized Hiraku project when you start
`hiraku dashboard --interactive` from that project. Interactive mode can
create records and launch configured entrypoints, so use it for projects whose
commands you trust.

## Start and sign in

From the Hiraku checkout, install the extras and start one example:

```bash
uv sync --all-extras
uv run --all-extras python scripts/use_case_dashboard.py --example minimal
```

The terminal prints the GUI URL, tracking location, access token, and demo
project path. Open the full **Hiraku GUI is now running on:** URL, including
`access_token`. The browser normally opens automatically. If you opened the
plain host URL or marimo asks for a token, use the value printed after
**Hiraku token is:**. This token allows local GUI writes; do not share it.

The demo defaults to `127.0.0.1:8765`. Digits also starts MLflow at
`127.0.0.1:5000`. Use `--port 8766` or `--mlflow-port 5001` when needed.

## Browse records and runs

The dashboard opens with the full-width record tree visible. **Record detail**,
**Create record**, **Launch run**, **Runs**, and **Plots** start collapsed;
click a section title to expand or collapse it. Create and launch sections
appear only in interactive mode. Run provenance is inside **Runs**, and selected
plot points are inside **Plots**. Expanded sections stay open during refreshes.

The record graph shows the selected task and its child experiments. Click a
node or use the record selector, then expand **Record detail** for effective fields,
inherited entrypoints, and their defining records. Search the graph to find a
record. Selecting a run exposes its record, entrypoint, commit attribution,
metrics, and any link to its tracking backend. **Open record** takes you to
the run's attributed record when it still exists.

The run-scope selector controls which results appear:

| Scope | Results shown |
| --- | --- |
| **This record** | Runs attributed to the selected record. |
| **This subtree** | Runs attributed to that record and its descendants, across their effective sources. |
| **All attributed runs in this source** | All Hiraku-attributed runs in the selected record's source, including records outside its subtree. |

For example, selecting the `digits` task with **This record** may show no
runs; choose **This subtree** to compare its `weak-l2` and `strong-l2`
experiments. **Progress** shows one summary metric per run over time (or run
order when timestamps are absent). **Pareto** compares two metrics using
their declared maximize/minimize directions. In Progress, **Progress metric
(Y axis)** selects the vertical metric; the horizontal axis is time or run
order. Pareto shows separate **X metric** and **Y metric** selectors and goals.
Select axes and parameters that make the runs comparable. Missing metric
values are omitted from plots and
reported, rather than treated as zero.

## Create a record

1. Open **Create record**. Enter a project-unique ID, choose a parent or
   **No parent (root)**, and fill in the relevant research fields.
2. Use **Preview** to check the current draft, proposed path, and validation messages.
   Preview writes no files.
3. Press **Create** to save it. The new record becomes selected in the GUI;
   the CLI's active record does not change. **Cancel** resets the form.

To configure fields beyond the form, expand **advanced YAML**. For example,
an `entrypoints` mapping defines commands that can later be launched. The
form's ID, parent, related IDs, Markdown body, and path cannot be overridden
through that YAML. Validation errors appear before creation, including a
duplicate ID or malformed YAML.

## Launch an entrypoint

1. Select a record with an effective entrypoint and open **Launch run**.
   Inherited entrypoints are listed with the record that defines them.
2. Select an entrypoint and dispatcher. Optionally provide **Argument
   overrides (YAML mapping)** and a **Commit ref override**.
3. Press **Preview launch** to inspect the rendered command, dispatcher,
   requested commit, and effective tracking source. Then press **Launch run**.

The overrides apply only to that launch. For example, `seed: 7` changes a
`seed` argument without editing the record. The local dispatcher executes
the current working tree and reports its actual Git HEAD; a requested commit
does not itself switch the checkout. A worktree dispatcher requires a
resolvable commit. If the selected record or entrypoint changes after your
preview, preview again before launching.

The entrypoint itself writes metrics to the tracking backend. Configuring a
backend for dashboard queries does not automatically set your script's
tracking environment. The demo helper aligns that environment for its Digits
and Iris launches.

## Inspect execution and results

The job panel shows the launch state, exit code, and stdout/stderr tails.
Those **Launch logs** are temporary and separate from tracking artifacts.
Only one launch runs at a time per dashboard server; there is no GUI cancel
button. After the job finishes, the dashboard refreshes results once. Use
**Refresh results** to query the backend again if needed. A process may finish
successfully without logging a run, and
a failed process may still leave a partial run. Use the run list and backend
link to verify what was actually recorded.

The general **Refresh** control also updates runs on demand; automatic
refresh is off by default. If a source query fails, the GUI displays the
error and marks retained results as **STALE** for that selection.

## Example walkthroughs

- **Minimal:** Select `my-task`, switch to **This subtree**, and inspect the
  `baseline` evaluation. Select `baseline` to preview and rerun `evaluate`.
- **Heart Disease:** Compare `logistic-baseline` and `random-forest` under
  `heart-disease` in subtree scope. Inspect `val/log_loss` and
  `val/accuracy` in Progress or Pareto.
- **Digits:** Select `digits` in subtree scope, compare the `weak-l2` and
  `strong-l2` runs, then open a run in the MLflow UI for its full training
  history and artifacts. The helper's initial runs use eight epochs.
- **Iris:** Explore the `iris` records and inherited W&B source. Runs appear
  only after W&B authentication and training with `--run-iris`, or after a
  later GUI launch that logs to the same entity and project.
- **Autoresearch:** The portable CPU demo works on macOS and Linux and opens
  `autoresearch` in subtree scope with three simulated runs. Inspect `val_bpb`
  in Progress or compare it with `peak_vram_mb` in Pareto. Full training is
  documented in the [Autoresearch example](../examples/autoresearch/README.md).

## Stop and reopen

Press **Ctrl-C** in the helper's terminal to stop the GUI and local MLflow
UI. The printed **Demo project** directory stays available. Reopen it from
the Hiraku checkout without repeating setup or initial training:

```bash
uv run --all-extras python scripts/use_case_dashboard.py --reopen "<demo-path>"
```

The new session prints a new token. Launch logs from the old GUI session do
not persist, but records and backend runs remain in the demo project.

## Troubleshooting

- **Port already in use:** Add `--port 8766`; for Digits, use
  `--mlflow-port 5001` if port 5000 is also occupied. Keep the two ports
  different.
- **Token rejected:** Use the full URL and current token printed by the
  running session. Reopening a demo creates a new token.
- **No runs visible:** Check the selected record and scope, then press
  **Refresh**. Iris starts without remote runs by default. Confirm that your
  entrypoint logged to the source shown in the record details.
- **No Launch run entrypoint:** Select a record with an effective entrypoint,
  or add an `entrypoints` mapping through **Create record → advanced YAML**.
- **Job succeeded but no result appears:** Check launch logs and the backend
  itself. Process success and tracker logging are separate events.

For command details and backend limitations, see the
[dashboard reference](../dashboard.md). The
[examples index](../examples/index.md) lists all startup
commands and options.
