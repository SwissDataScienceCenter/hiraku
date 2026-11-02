# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Breaking:** Query the record knowledge base through fmql. Configure its
  user-owned location with required `records_path` in `hiraku.yml`; record creation now
  requires an explicit destination. Remove field inheritance and enforced file
  layouts. Reparenting updates metadata without moving files. Add read-only
  fmql queries to the CLI and MCP server, use native fmql workspaces in Python
  with validated `Record.load/create/save/update/delete` lifecycle methods, and remove
  folder-based MCP source attachments.
- **Breaking:** Replace named record entrypoints and `hiraku launch` with `hiraku run --record ID -- COMMAND [ARGS...]`, which runs the supplied argv in the foreground with record and Git attribution. Existing `entrypoints` fields are no longer accepted ([issue #90](https://github.com/SwissDataScienceCenter/hiraku/issues/90), [PR #112](https://github.com/SwissDataScienceCenter/hiraku/pull/112)).
- **Breaking:** Configure `tracking_backend` in `hiraku.yml` instead of on a record. Tracking backend configuration is project-wide ([PR #112](https://github.com/SwissDataScienceCenter/hiraku/pull/112)).
- Render human-readable CLI tables with Rich; JSON output remains available for programmatic use ([PR #112](https://github.com/SwissDataScienceCenter/hiraku/pull/112)).
- Separate dashboard performance and trade-off plots into independently placeable components and named cells with their own controls, removing the analysis mode selector.
- Exclude generated Marimo and Studio session/build caches from distribution archives.
- Extract reusable dashboard widgets and rendering into `hiraku.dashboard.components`, keeping notebook cells focused on data and reactive selection wiring.
- Move dashboard branding and layout into the Studio HTML, CSS, and assets, leaving notebook cells for data and interactive outputs.
- `hiraku dashboard` automatically selects a free port when `--port` is omitted, in both view and edit modes.
- The dashboard is served through a [marimo-studio](https://marimo-team.github.io/marimo-studio/) view with the same layout, and `hiraku dashboard --edit` opens the copy in marimo-studio. `--init` also copies the view to `hiraku/dashboard/studio/`. The `dashboard` extra now installs `marimo-studio`.
- **Breaking:** `hiraku init` initializes a project independently of Git. Add `--git` to initialize the code repository explicitly.
- **Breaking:** `hiraku example create NAME [PATH]` copies a bundled example into `./NAME` by default without Git history or network access. Use `--with-results`, `--with-generator`, and `--generate` with `example create`. `hiraku init [PATH]` targets the current or explicit directory, and repeating plain initialization leaves the project unchanged ([issue #104](https://github.com/SwissDataScienceCenter/hiraku/issues/104)).
- **Breaking:** `hiraku runs` and `Record.runs()` now list only runs attributed to the selected record (the active record, or `--record ID`). Previously they returned every attributed run from the record's tracking source ([issue #66](https://github.com/SwissDataScienceCenter/hiraku/issues/66)).
- **Breaking:** Represent research material as `Record` objects; use `type: context` for supporting context. Remove `hiraku.context`, `Context`, the `hiraku context` CLI group, context-specific MCP tools and resources, and the separate `hiraku/contexts/` directory ([issue #133](https://github.com/SwissDataScienceCenter/hiraku/issues/133)).

### Added

- Add `hiraku example list` to list bundled examples with their descriptions.
- Add project-local Codex and Claude Code hooks installed by `hiraku init` to attribute workflow event attempts to client conversations. Generated MCP and hook commands are portable: `uv run hiraku` when the project contains `uv.lock`, otherwise `hiraku`. Session history records one entry per event attempt with its outcome. Resubmissions for answered instructions return `stale` with the current instruction instead of acknowledging identical duplicates as accepted.
- Add `Run.duration`, a run's wall-clock length in seconds. The CSV backend reads an optional `duration` column, MLflow uses the run's end minus start time, and W&B its `_runtime` summary. `hiraku runs` shows it, and the dashboard adds a Duration column, run detail and plot tooltip next to the start time.
- Add optional, collapsible dashboard record navigation sharing selection with the run table and highlighting a selected run's owning record.
- Add a dashboard Markdown viewer for records with expandable front matter, and show the selected record's Markdown or a selected run's owning record in the dashboard.
- Add optional dashboard Markdown editing with explicit Save/Cancel, session-local drafts, and detection of external record changes before saving.
- Add `Record.save_markdown()` for saving a replacement body with metadata preserved and checking for external changes, independent of dashboard dependencies.
- Workflow instructions now separate inline `data` from research record references. MCP returns native resource links for attached records. **Breaking:** move previous arbitrary instruction `context` values to `data`; old saved instructions are not migrated. Instruction context references now accept record IDs only.
- Add the local stdio MCP server (`hiraku mcp`) with record CRUD, attributed launches, run queries, source resources, and an evidence-based proposal prompt; `hiraku init` registers it for Claude Code and Codex ([issue #80](https://github.com/SwissDataScienceCenter/hiraku/issues/80)).
- Add project-defined workflows under `hiraku/workflows/<id>/`: Python state machines with typed mutable state, annotated event inputs, node instructions, and immutable graphs, or Markdown-only prompts with arguments inferred from YAML scalar defaults. Import APIs from their defining modules under `hiraku.workflows`.
- Add persistent workflow sessions with validated events, stored instructions, retry-safe continuation, locking, inspection, and cancellation. `Session.start()` returns the saved session and raises when start fails; execute `session.run_event()` and `session.cancel()` inside `with session.lock():`. Events return an `EventResult` with only `status` and `message`, also stored in history; the session holds the current revision. Retrieve assignments with `session.get_instruction()`. Stored sessions can be inspected and cancelled without loading workflow code.
- Add `hiraku workflow list|start|send-event|graph`, `hiraku session list|show|cancel`, and workflow/session MCP tools, prompts, and resources. Conversational MCP attaches the current instruction to every workflow start and event response. Workflow tools are fixed when the server starts; bound project configuration reloads on access.
- Add the bundled `collaborative` research workflow and the `workflows` example with count-steps, greet, and scheduler. Autoresearch becomes a Markdown-only workflow. Approval requests are conversational contracts.
- Record bodies are `hiraku.markdown.Markdown` strings that select a section by its heading line, e.g. `record.markdown.section("## Change")`, including its subsections. Headings are found with a CommonMark parser (the new `markdown-it-py` dependency), so headings in code blocks, lists, and block quotes do not start sections.
- Add a trade-off view for comparing two metrics across project runs in Marimo Studio and highlighting runs with strong trade-offs.
- Add `Record.resolve_tracking_backend()` to build the tracking backend inherited by a record; `TrackingBackend.list_runs()` now lists all attributed runs when `record_id` is omitted.
- Add `hiraku runs --all` to list all attributed runs from the tracking source resolved for a record ([issue #66](https://github.com/SwissDataScienceCenter/hiraku/issues/66)).
- Add `hiraku dashboard`, a local read-only dashboard with a record and run tree, run details with a link to the tracking backend, and a performance plot of runs in execution order. Install it with the `dashboard` extra.
- Add `hiraku dashboard --init` to copy the dashboard notebook into the project, `--edit` to open that copy in the marimo editor, and `--include-code` to show the notebook code in the read-only dashboard. The notebook exposes runs and records, with their place in the tree, as the Arrow-backed `runs_df` and `records_df` DataFrames.
- Add the MLflow `ui_url` option; MLflow runs now link to the MLflow web UI, also when `tracking_uri` is a credential-free HTTP(S) server.

### Fixed

- The Heart Disease and minimal example writers and the autoresearch dispatcher now record when a run started instead of when it finished in `timestamp`, which the dashboard shows as the start time, and write the new `duration` column. **Breaking:** their CSV header gains `duration`, so append to a new `runs.csv` after updating them.
- Embed dashboard logos in the HTML entry document so Studio can build the branded view without rejecting local image references.
- Fix `hiraku record list`, `context list`, `program list`, `action list` (and other table output) crashing when there are no rows ([issue #96](https://github.com/SwissDataScienceCenter/hiraku/issues/96)).
- Reject the reserved workflow ID `runs`, validate instructions before saving, and hide instructions for incompatible or unavailable active workflows. Collaborative research now quotes launch arguments and requires matching entrypoint attribution for run evidence.
- Fix `hiraku record list`, `context list` (and other table output) crashing when there are no rows ([issue #96](https://github.com/SwissDataScienceCenter/hiraku/issues/96)).
- Read every page of MLflow experiments and runs; projects with more than 1,000 runs previously lost the rest.

### Removed

- **Breaking:** Remove `BaseRecord`; all research material uses `Record`, including task, experiment, context, and literature records.
- **Breaking:** Remove Programs and Actions, including `hiraku program`, `hiraku action`, `hiraku.agent.Program`, `hiraku.agent.Action`, their `hiraku://programs` and `hiraku://actions` MCP resources, and their bundled resources.
- **Breaking:** Remove the `default_program` configuration key; `hiraku.yml` files that still contain it are rejected with a migration message.
- **Breaking:** Remove unused `GitRepository` branch-management methods, `head`, `is_ancestor`, `parent_records`, and `child_records`, and the project-level branch-name template. Use Git directly for branch operations; record-local `branch` metadata is unchanged. Existing configuration keys remain preserved as extra metadata.
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
