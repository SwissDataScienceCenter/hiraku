# Stage 4 Implementation Plan: Experiment Dashboard

## 1. Purpose

Stage 4 adds a task-scoped dashboard for visually reviewing the task document, experiment records,
run evidence, metric progress, and Git-derived experiment lineage.

The dashboard is a strictly read-only presentation layer over the existing Hiraku data model. It
must not introduce a second experiment registry, reinterpret scientific decisions, expose workflow
mutations, or make table and MLflow workflows behave differently. Its primary entrypoint is:

```bash
hiraku dashboard [task]
```

Stage 4 does not add cross-task experiment or run listings, run mutation, experiment editing, launch
or scheduler controls, review publication controls, model-registry behavior, HPO, or new tracking
providers.

## 2. Compatibility baseline

The dashboard builds on the Stage 1–3 contracts:

- a task is selected explicitly or inferred from the current task or experiment branch;
- `task.md` is the canonical reviewed task document;
- experiment YAML and Markdown records normalize to the same schema;
- experiment lineage and task-branch containment are derived from exact Git ancestry;
- scientific `decision` is distinct from Git containment in the task branch;
- table tracking remains the default, while local and remote MLflow remain optional;
- ordered task metrics and their `minimize` or `maximize` directions determine default presentation;
- exact identifiers and commits remain available in underlying data even when the UI abbreviates
  them;
- malformed or incomplete provenance is surfaced rather than silently repaired; and
- remote MLflow failures remain actionable and never fall back to local SQLite.

Before implementation, run the complete Stage 3 suite with the MLflow extra and treat it as the
regression boundary.

The README currently lists a graphical dashboard under both Stage 4 and the deferred section. Stage
4 makes the task-scoped dashboard current scope, so implementation should remove that obsolete
deferred dashboard item while leaving broader project interfaces and cross-task visualization
deferred.

## 3. Stage 4 deliverables

Stage 4 delivers:

- the `hiraku dashboard [task]` CLI command;
- a backend-neutral dashboard data model assembled from current task, experiment, run, and Git
  services;
- a local dashboard containing task documentation, experiment and run tables, lineage, and an
  interactive metric-over-time chart;
- an Altair/Vega-Lite chart specification with a task-aware performance frontier;
- experiment and experiment-status filters shared by the chart and run table, with the frontier
  recomputed from visible runs;
- clear empty, warning, invalid-provenance, and backend-error states;
- dashboard documentation using the heart-disease and autoresearch examples;
- a `dashboard` installation extra with pinned, bundled browser assets and offline operation; and
- unit, integration, rendering-contract, packaging, and manual browser tests.

## 4. Define the CLI contract

Add `dashboard` as a top-level command:

```text
hiraku dashboard [task]
```

Task resolution must use the same selector and branch inference rules as `hiraku task`, `hiraku
experiment list`, and `hiraku runs`. A conflicting or unresolved task fails before starting or
opening any dashboard.

The command starts a long-running loopback web server, opens the dashboard in the default browser,
and keeps running until interrupted. It provides a manual refresh control but no file watching,
background polling, or automatic reload.

Use this CLI shape:

```text
hiraku dashboard [task] [--host <host>] [--port <port>] [--no-open]
```

- `--host` defaults to `127.0.0.1`; a non-loopback value is an explicit user choice;
- `--port` defaults to an available ephemeral port, avoiding a fixed-port collision;
- `--no-open` starts the server and prints its URL without opening a browser; and
- `Ctrl-C` stops the server cleanly.

The server has no endpoint that mutates repository, Git, tracking, review, or other external state.
Manual refresh rebuilds its in-memory dashboard snapshot from current task, experiment, Git, and
tracking data, then updates the page. A refresh error is displayed without replacing the last valid
snapshot, leaking sensitive diagnostics, or falling back to another tracking store.

The command must:

1. load and validate project configuration;
2. resolve exactly one task;
3. assemble a complete dashboard snapshot through internal Python APIs rather than scraping CLI
   tables or JSON text;
4. fail with the normal Hiraku error style when task, experiment, Git, or tracking data cannot be
   loaded;
5. bind the server before opening the browser and print the exact URL;
6. serve the dashboard and bundled assets without external network access; and
7. stop predictably on interruption and clean up server resources.

Acceptance criteria:

- explicit task selection works from any repository branch;
- implicit selection works from canonical task and experiment branches;
- selector errors have no browser or server side effect;
- manual refresh reflects changes to records, Git ancestry, and run storage;
- `--no-open` supports headless and remote-terminal use;
- the default server listens only on loopback;
- table mode does not import MLflow or dashboard dependencies before the command needs them; and
- existing commands and aliases remain unchanged.

## 5. Introduce a dashboard view model

Create one normalized, serializable dashboard model instead of allowing UI components to call
storage and Git helpers independently. It should contain:

- task metadata and the complete Markdown body;
- declared metrics in order with their directions;
- normalized experiment records, including status, decision, summary, hypothesis, conclusion,
  branch, bound commit, timestamps, run count, task-branch containment, and derived parents;
- normalized runs, including identifiers, experiment association, exact commit, timestamp, status,
  parameters, metrics, tags when applicable, and provenance warnings;
- available numeric metric names;
- selected/default metric and direction;
- chart points and precomputed frontier values; and
- dashboard-level warnings and backend information that are safe to display.

Reuse the same task parser, experiment enrichment, run backend, metric declaration, Git ancestry, and
commit-mismatch logic used by the CLI. Refactor shared logic where needed rather than duplicating it
inside dashboard code.

The view model must preserve exact values. Abbreviation and formatting belong in the rendered view.
Table and MLflow details may remain namespaced in expandable run details, but the primary model and
chart must be backend-neutral.

Acceptance criteria:

- the same fixture produces equivalent dashboard semantics under table and MLflow tracking;
- task metric declarations control default metric and direction;
- fallback metric discovery is deterministic when declarations are absent;
- runs with missing or non-numeric selected metrics remain inspectable but are excluded from the
  numeric frontier calculation;
- unknown, invalid, or unassociated runs remain explicitly identifiable; and
- no credential-bearing MLflow URI, environment value, or exception detail enters the model.

## 6. Dashboard information architecture

Use a single task page with these regions.

### 6.1 Task header and document

Show task ID, summary, goal, branch, declared metrics, tracking backend, and review identity when
present. Render the complete `task.md` beneath or beside the structured summary so the canonical
review document is readable without opening another file.

Markdown rendering must escape or sanitize unsafe embedded HTML and must not execute scripts from a
task document.

### 6.2 Controls and state summary

Provide:

- a selected-metric control populated from declared and discovered numeric metrics;
- an experiment filter supporting one, several, or all experiments;
- an experiment-status filter based only on experiment record status;
- a visible indication of the selected metric's direction; and
- counts for experiments, visible runs, omitted/invalid runs, and active warnings.

Changing either filter updates the chart and run table consistently and recomputes the frontier from
only visible numeric runs. Run execution status remains visible metadata and is not a separate Stage
4 filter. Filtering is browser view state only and must never change Hiraku records.

### 6.3 Experiment table

Show at least:

- experiment ID;
- summary;
- status;
- decision;
- branch with the existing task-containment indication;
- abbreviated bound commit;
- run count;
- selected-metric best value where available; and
- created and updated timestamps.

Make the hypothesis, conclusion, exact commit, entrypoints, parent experiments, and provenance
warnings available through an expanded detail view or tooltip without making the default table
unreadable.

Sort deterministically. The default should prioritize recency or experiment creation order rather
than silently treating the best metric as the semantic experiment decision.

### 6.4 Run table

Show the compact information used by `hiraku runs`: abbreviated run and commit IDs, experiment,
timestamp, status where available, selected metrics, useful parameters, and commit-mismatch state.
Sort numeric rows by the selected metric and declared direction, while retaining missing/non-numeric
rows after ranked rows.

Hover or expansion should expose exact IDs, exact commit, all metrics, parameters, backend metadata,
experiment summary, hypothesis, status, decision, and warnings. Avoid duplicating nested MLflow
objects in the default view.

### 6.5 Lineage view

When the task contains branched experiments, visualize the experiment DAG derived from Git ancestry.
Each experiment node should show its ID, status or decision, branch, and task-containment state.
Edges represent the same derived parent relationships exposed by experiment JSON.

The view must distinguish:

- scientific acceptance from containment in the task branch;
- an unbound experiment from one whose commit cannot be resolved;
- a root experiment from an experiment whose parent is outside the visible registry; and
- exact ancestry from squash-merge or cherry-pick situations that cannot be inferred as containment.

For a purely linear task or one without resolvable lineage, show a concise empty/not-applicable state
rather than inventing relationships.

## 7. Metric timeline and performance frontier

Build the interactive chart with Altair and Vega-Lite.

For each run with a numeric selected metric and parseable timestamp, plot:

- time on the horizontal axis;
- selected metric value on the vertical axis;
- experiment identity by color or another accessible channel; and
- enough encoded fields for a hover tooltip containing run details and experiment details.

Overlay the performance frontier as a step line computed in chronological order. The natural Hiraku
definition is the cumulative best observed value: cumulative minimum for a metric declared
`minimize`, and cumulative maximum for one declared `maximize`. Runs tied at the current best remain
visible as points while the frontier value stays unchanged.

The chart should:

- default to the task's first declared metric and direction;
- update when the selected metric changes;
- respond to experiment and experiment-status filters, recomputing the frontier from the visible
  numeric runs;
- keep timestamp ordering independent of experiment-record order;
- visibly distinguish invalid, crashed, or commit-mismatched runs when they are included; and
- present an explanatory empty state when no numeric points are available.

The Python layer should calculate or validate frontier data so its behavior can be unit-tested
without a browser. The Vega-Lite specification remains responsible for interactive selections,
tooltips, legends, scales, and accessible labels.

Acceptance criteria:

- minimize and maximize frontiers are correct for unsorted inputs, ties, and multiple experiments;
- filtering produces a well-defined frontier rather than retaining hidden points;
- switching metrics also switches direction and axis/tooltip labels;
- exact run and experiment details are available on hover;
- chart construction is deterministic for snapshot testing; and
- no metric history beyond the run value already returned by Hiraku is implied for MLflow.

## 8. Backend and error behavior

### 8.1 Table tracking

Read the existing task-local CSV/TSV data through `TableBackend`. Treat non-provenance columns as
result fields under the current table contract. Do not write, normalize, or repair the run file.

### 8.2 Local and remote MLflow

Load MLflow lazily through `MlflowBackend`, use the configured local SQLite database or remote URI,
and honor the task's `mlflow_experiment`. Preserve current provenance filtering and make omitted-run
warnings visible. A connectivity or authentication failure should stop initial dashboard creation
with the same redacted actionable error used by `hiraku runs`; it must not start an empty dashboard
or fall back to local storage.

### 8.3 Partial local data

The dashboard should still render when a valid task has no experiments, no runs, no declared metrics,
or no experiment branches. Each region needs a specific empty state. Malformed task or experiment
records remain hard validation errors rather than silently disappearing.

## 9. Rendering, security, and accessibility

Keep the dashboard on `127.0.0.1` by default. A non-loopback `--host` is an explicit user choice and
must produce a clear warning that Hiraku provides no authentication or transport security.

The implementation must:

- escape task, experiment, run, parameter, tag, and warning text before inserting it into HTML;
- sanitize task Markdown and disallow executable embedded HTML or JavaScript;
- avoid placing MLflow credentials, process environment values, or full remote exceptions in page
  source;
- expose only read-only page, asset, snapshot, and refresh endpoints;
- provide labels, keyboard-reachable controls, readable contrast, and tooltips that are not the only
  source of essential information;
- behave on a normal laptop viewport, with horizontal scrolling or progressive detail for wide
  tables; and
- show exact identifiers in detail views while abbreviating them in dense summaries.

## 10. Packaging and dependency boundary

Add Altair and the minimum server, rendering, sanitization, and Markdown dependencies behind the
`dashboard` optional extra. Running the command without that extra must fail with an actionable
instruction to install `hiraku[dashboard]`. MLflow remains a separate optional concern: table
dashboards require only `hiraku[dashboard]`, while MLflow dashboards require both the dashboard and
MLflow extras.

Bundle pinned Vega, Vega-Lite, Vega-Embed, HTML, CSS, JavaScript, icons, and templates as package
resources and verify them in the built wheel and source distribution. The dashboard must not load
code, fonts, styles, or other browser assets from a CDN. Avoid introducing a general frontend build
toolchain unless it is demonstrably needed for producing the pinned package resources.

Table and local-MLflow dashboards must operate fully offline. Remote MLflow naturally requires access
to the configured server, but rendering itself must not contact any other service.

## 11. Documentation and examples

Update the README Stage 4 section and CLI reference with the resolved command and installation
contract. Remove the obsolete deferred-dashboard statement.

Add a dashboard section to `docs/advanced.md` covering:

- launch and shutdown behavior;
- task selection;
- metric, experiment, and experiment-status filters;
- frontier meaning for minimize and maximize metrics;
- the difference between decision and task-branch containment;
- table, local MLflow, and remote MLflow behavior;
- invalid provenance and commit-mismatch warnings; and
- local server security, manual refresh, and fully offline browser assets.

Use the heart-disease example for the normal dashboard walkthrough:

- `logistic-baseline` and `random-forest` provide comparison points;
- `stronger-regularization` provides a small branch and lineage example;
- `val/log_loss` demonstrates a minimizing frontier; and
- `val/accuracy` demonstrates metric switching and a maximizing direction.

Use autoresearch only to validate a longer metric history and frontier, not as the introductory
dashboard setup. Smoke remains an installation/tracking check and does not need a dashboard narrative.

## 12. Verification strategy

### 12.1 Unit tests

- task selector and CLI option resolution;
- dashboard view-model construction;
- ordered and fallback metric discovery;
- minimize/maximize frontier calculation, ties, invalid values, and unsorted timestamps;
- experiment/experiment-status filtering semantics;
- best-per-experiment metric calculation;
- lineage node and edge construction from derived parents;
- task-containment and scientific-decision separation;
- HTML escaping, Markdown sanitization, and credential redaction; and
- all empty and warning states.

### 12.2 Integration tests

- `hiraku dashboard heart-disease` under table tracking;
- current-task inference on task and experiment branches;
- a linear task with no lineage view;
- a branched task with parent edges and containment markers;
- local SQLite MLflow dashboard construction;
- mocked remote MLflow construction and redacted failure behavior;
- filters updating chart and table state consistently;
- manual refresh rebuilding the snapshot without watchers or polling;
- `--no-open`, ephemeral-port selection, clean interruption, and non-loopback warnings;
- malformed records failing before browser/server side effects; and
- optional dependency errors naming the correct installation extra.

### 12.3 Rendering-contract tests

- generated HTML contains the task, experiment, run, and chart regions;
- emitted Vega-Lite specifications validate against the supported schema;
- tooltips contain the promised run and experiment fields;
- accessibility labels and keyboard-reachable controls exist;
- unsafe Markdown/record content is escaped or sanitized;
- wide tables and empty states render without structural failure; and
- pinned browser assets load from the package with external network access disabled.

Prefer semantic DOM and Vega specification assertions over brittle pixel snapshots. Use a small
browser smoke test for interaction, hover details, filtering, and shutdown behavior. Manual visual
review should cover heart-disease and the longer autoresearch timeline.

### 12.4 Release checks

- Ruff formatting and linting;
- complete default suite without dashboard or MLflow extras;
- complete suite with dashboard only;
- complete suite with dashboard and MLflow;
- package build and installed-resource inspection;
- supported Python versions from CI;
- no generated dashboards, run tables, databases, logs, credentials, or browser profiles in the
  package; and
- README and advanced-documentation command review.

## 13. Delivery sequence

Implement Stage 4 in this order:

1. capture the Stage 3 regression baseline;
2. define normalized dashboard and chart data models;
3. refactor and reuse task, experiment, run, metric, and lineage services;
4. implement and unit-test visible-run frontier and experiment-status filter semantics;
5. add the long-running loopback server, CLI options, browser opening, and manual refresh;
6. implement task, experiment, run, and warning regions as read-only views;
7. implement the Altair/Vega-Lite timeline and interactions;
8. implement the lineage view and empty/error states;
9. bundle and pin browser assets for offline operation;
10. harden escaping, Markdown handling, credential redaction, and loopback behavior;
11. add heart-disease and autoresearch documentation;
12. add integration and browser smoke tests; and
13. run all dependency-matrix, packaging, and manual visual checks.

## 14. Stage 4 completion criteria

Stage 4 is complete when:

- `hiraku dashboard [task]` reliably resolves and displays one task;
- the dashboard shows the canonical task document, experiments, runs, metrics, and warnings;
- branched experiments have an accurate Git-derived lineage view;
- the selected metric timeline and direction-aware performance frontier are correct and interactive;
- experiment and experiment-status filters update the applicable evidence consistently;
- hover or expanded details connect every plotted run to its experiment and exact provenance;
- table, local SQLite MLflow, and remote MLflow share the same dashboard semantics;
- local rendering is safe, accessible, and does not depend on external browser assets;
- the base Hiraku installation and existing CLI behavior remain compatible; and
- automated tests, package builds, and manual visual verification pass.
