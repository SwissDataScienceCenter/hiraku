# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Separate dashboard performance and trade-off plots into independently placeable components and named cells with their own controls, removing the analysis mode selector.
- Exclude generated Marimo and Studio session/build caches from distribution archives.
- Extract reusable dashboard widgets and rendering into `hiraku.dashboard.components`, keeping notebook cells focused on data and reactive selection wiring.
- Move dashboard branding and layout into the Studio HTML, CSS, and assets, leaving notebook cells for data and interactive outputs.
- `hiraku dashboard` automatically selects a free port when `--port` is omitted, in both view and edit modes.
- The dashboard is served through a [marimo-studio](https://marimo-team.github.io/marimo-studio/) view with the same layout, and `hiraku dashboard --edit` opens the copy in marimo-studio. `--init` also copies the view to `hiraku/dashboard/studio/`. The `dashboard` extra now installs `marimo-studio`.
- **Breaking:** `hiraku runs` and `Record.runs()` now list only runs attributed to the selected record (the active record, or `--record ID`). Previously they returned every attributed run from the record's tracking source ([issue #66](https://github.com/SwissDataScienceCenter/hiraku/issues/66)).
- **Breaking:** Rename the `hiraku.agent` module to `hiraku.context`; import `Context`, `load_context`, and `list_contexts` from `hiraku.context`.

### Added

- Add `Run.duration`, a run's wall-clock length in seconds. The CSV backend reads an optional `duration` column, MLflow uses the run's end minus start time, and W&B its `_runtime` summary. `hiraku runs` shows it, and the dashboard adds a Duration column, run detail and plot tooltip next to the start time.
- Add optional, collapsible dashboard record navigation sharing selection with the run table and highlighting a selected run's owning record.
- Add a generic dashboard Markdown viewer accepting any `BaseRecord` subtype with expandable front matter, and show the selected record's Markdown or a selected run's owning record in the dashboard.
- Add optional dashboard Markdown editing with explicit Save/Cancel, session-local drafts, and detection of external record changes before saving.
- Add `BaseRecord.save_markdown()` for saving a replacement body with metadata preserved and checking for external changes, independent of dashboard dependencies.
- Workflow instructions now separate inline `data` from research `context` references (`contexts` and `records`). MCP returns native resource links for attached research material. **Breaking:** move previous arbitrary instruction `context` values to `data`; old saved instructions are not migrated.
- Add the local stdio MCP server (`hiraku mcp`) with record and context CRUD, attributed launches, run queries, curated source resources, and an evidence-based proposal prompt; `hiraku init` registers it for Claude Code and Codex ([issue #80](https://github.com/SwissDataScienceCenter/hiraku/issues/80)).
- Add project-defined workflows under `hiraku/workflows/<id>/`: Python state machines with typed mutable state, annotated event inputs, node instructions, and immutable graphs, or Markdown-only prompts with arguments inferred from YAML scalar defaults. Import APIs from their defining modules under `hiraku.workflows`.
- Add persistent workflow sessions with validated events, stored instructions, retry-safe continuation, locking, inspection, and cancellation. `Session.run_start()` and `session.run_event()` return `EventResult`; retrieve assignments separately with `session.get_instruction()`. Sessions can be inspected and cancelled without loading workflow code.
- Add `hiraku workflow list|start|send-event|graph`, `hiraku session list|show|cancel`, and workflow/session MCP tools, prompts, and resources. Conversational MCP attaches instructions after accepted or stale submissions. Workflow tools are fixed when the server starts; bound project configuration reloads on access.
- Add the bundled `collaborative` research workflow and the `workflows` example with count-steps, greet, and scheduler. Autoresearch becomes a Markdown-only workflow. Approval requests are conversational contracts.
- Record bodies are `hiraku.markdown.Markdown` strings that select a section by its heading line, e.g. `record.markdown.section("## Change")`, including its subsections. Headings are found with a CommonMark parser (the new `markdown-it-py` dependency), so headings in code blocks, lists, and block quotes do not start sections.
- Add a trade-off view for comparing two metrics across project runs in Marimo Studio and highlighting runs with strong trade-offs.
- Add `Record.resolve_tracking_backend()` to build the tracking backend inherited by a record; `TrackingBackend.list_runs()` now lists all attributed runs when `record_id` is omitted.
- Add `hiraku runs --all` to list all attributed runs from the tracking source resolved for a record ([issue #66](https://github.com/SwissDataScienceCenter/hiraku/issues/66)).
- Add `hiraku dashboard`, a local read-only dashboard with a record and run tree, run details with a link to the tracking backend, and a performance plot of runs in execution order. Install it with the `dashboard` extra.
- Add `hiraku dashboard --init` to copy the dashboard notebook into the project, `--edit` to open that copy in the marimo editor, and `--include-code` to show the notebook code in the read-only dashboard. The notebook exposes runs and records, with their place in the tree, as the Arrow-backed `runs_df` and `records_df` DataFrames.
- Add the MLflow `ui_url` option; MLflow runs now link to the MLflow web UI, also when `tracking_uri` is a credential-free HTTP(S) server.
- Add a Snakemake tracking source and `hiraku snakemake run` to capture a workflow execution's declared inputs and outputs, observed file states, requested targets, result, and commit metadata.
- Add structured file inputs and outputs to normalized runs; `hiraku runs --json` and the dashboard run details now show declared and observed files separately.

### Fixed

- The Heart Disease and minimal example writers and the autoresearch dispatcher now record when a run started instead of when it finished in `timestamp`, which the dashboard shows as the start time, and write the new `duration` column. **Breaking:** their CSV header gains `duration`, so append to a new `runs.csv` after updating them.
- Embed dashboard logos in the HTML entry document so Studio can build the branded view without rejecting local image references.
- Fix `hiraku record list`, `context list`, `program list`, `action list` (and other table output) crashing when there are no rows ([issue #96](https://github.com/SwissDataScienceCenter/hiraku/issues/96)).
- Reject the reserved workflow ID `runs`, validate instructions before saving, and hide instructions for incompatible or unavailable active workflows. Collaborative research now quotes launch arguments and requires matching entrypoint attribution for run evidence.
- Fix `hiraku record list`, `context list` (and other table output) crashing when there are no rows ([issue #96](https://github.com/SwissDataScienceCenter/hiraku/issues/96)).
- Read every page of MLflow experiments and runs; projects with more than 1,000 runs previously lost the rest.

### Removed

- **Breaking:** Remove Programs and Actions, including `hiraku program`, `hiraku action`, `hiraku.agent.Program`, `hiraku.agent.Action`, their `hiraku://programs` and `hiraku://actions` MCP resources, and their bundled resources. Contexts are unchanged.
- **Breaking:** Remove the `default_program` configuration key; `hiraku.yml` files that still contain it are rejected with a migration message.
- **Breaking:** Remove the `hiraku runs list` subcommand; use `hiraku runs` ([issue #66](https://github.com/SwissDataScienceCenter/hiraku/issues/66)).
- **Breaking:** Remove `TrackingBackend.list_all_recorded_runs()` and the `backend` argument of `Record.runs()`; use `record.resolve_tracking_backend().list_runs()`. Replace `select_backend` with `Record.resolve_tracking_backend()` and rename `backend_for` to `create_backend`.
- **Breaking:** Remove the undocumented `mlflow_experiment` record field fallback; set `experiment_name` in the MLflow `tracking_backend` mapping instead.

## [0.1.1] - 2026-09-30

### Added

- Add record trees with inherited fields and entrypoints, related-record links, and status in record listings ([PR #58](https://github.com/SwissDataScienceCenter/hiraku/pull/58)).
- Roll back reparenting changes when deleting old record files fails, restoring already-removed files and cleaning up new copies; report when rollback is incomplete ([PR #58](https://github.com/SwissDataScienceCenter/hiraku/pull/58)).
- Add project initialization from bundled templates, including the minimal and heart-disease examples ([PR #35](https://github.com/SwissDataScienceCenter/hiraku/pull/35)).
- Add project-local agent skill, program, action, and dispatcher resources during `hiraku init`.
- Add pre-commit hooks for Ruff formatting and linting ([issue #18](https://github.com/SwissDataScienceCenter/hiraku/issues/18), [PR #23](https://github.com/SwissDataScienceCenter/hiraku/pull/23)).
- Add semantic-versioning release configuration with `bump-my-version` ([issue #17](https://github.com/SwissDataScienceCenter/hiraku/issues/17)).
- Add CI runs for pull requests and pushes to `main`, plus wheel and source distribution publication from version tags ([issue #42](https://github.com/SwissDataScienceCenter/hiraku/issues/42)).

### Changed

- Rename the tracked-run query command to `hiraku runs`; `hiraku runs list` is also accepted.
- **Breaking:** Replace separate task and experiment models and commands with the research `Record` model and `hiraku record` commands. Generic Markdown persistence is now `BaseRecord`; task/experiment data and Python imports have no compatibility layer or migration tooling ([PR #58](https://github.com/SwissDataScienceCenter/hiraku/pull/58)).
- Query each distinct tracking source once when listing runs across a record subtree, then filter by record attribution in the shared tracking layer ([PR #58](https://github.com/SwissDataScienceCenter/hiraku/pull/58)).
- Improve `hiraku init` to support initializing a target directory and report paths relative to the initialized project.
- Update the CLI, examples, and agent guidance for record-based workflows ([PR #58](https://github.com/SwissDataScienceCenter/hiraku/pull/58)).
- Update the autoresearch example to use an upstream project with a Hiraku dispatcher wrapper ([PR #25](https://github.com/SwissDataScienceCenter/hiraku/pull/25)), and validate required system utilities ([issue #37](https://github.com/SwissDataScienceCenter/hiraku/issues/37), [PR #38](https://github.com/SwissDataScienceCenter/hiraku/pull/38)).
- Update version bumps to create the release commit and tag locally before pushing both from `main` ([issue #42](https://github.com/SwissDataScienceCenter/hiraku/issues/42)).
- Keep the editable package version in `uv.lock` synchronized during release preparation ([issue #42](https://github.com/SwissDataScienceCenter/hiraku/issues/42)).
- Build distributions from version tags and attach them to the GitHub Release ([issue #42](https://github.com/SwissDataScienceCenter/hiraku/issues/42)).

### Removed

- Remove the standalone autoresearch example implementation in favor of the upstream-based example ([PR #25](https://github.com/SwissDataScienceCenter/hiraku/pull/25)).

## [0.1.0] - 2026-09-10

### Added

- Add the initial Hiraku CLI for managing projects, tasks, experiments, entrypoints, launches, and tracked runs.
- Add CSV, MLflow, W&B, and custom tracking-backend support.
- Add project initialization and configuration through `hiraku init`.
- Add structured Python APIs and generated reference documentation.
- Add the launcher command preview with `--dry-run` ([PR #16](https://github.com/SwissDataScienceCenter/hiraku/pull/16)).
- Add the Iris/W&B, MLflow, and autoresearch examples.

### Changed

- Replace the initial proof-of-concept data model with validated Pydantic schemas and a generic tracking-backend interface.
- Expand documentation with concepts, guides, examples, and custom-backend instructions.

### Removed

- Remove obsolete implementation plans and draft agent documentation.
