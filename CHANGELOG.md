# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Removed

- **Breaking:** Remove the top-level `hiraku status` command.

### Changed

- Keep PSI/J launcher files and default detached job data in the project's
  `.hiraku/psij/` directory. **Breaking:** `run_local()` requires a project and
  always records submission history.
  **Breaking:** Submission history requires `executor_work_directory` alongside
  the executor name and URL to reconstruct the executor's auxiliary storage.
- **Breaking:** Move `hiraku runs` to `hiraku leaderboard runs`.
- **Breaking:** Commands receive `HIRAKU_RECORD_ID` and `HIRAKU_JOB_ID`; tracking
  code logs both directly on runs. Leaderboards query source attribution without
  local job history. Git environment variables are no longer injected;
  records and tracking runs retain user-managed code provenance. Tracking
  destinations remain user-managed.
- **Breaking:** Replace record entrypoints and `hiraku launch` with
  `hiraku run --record ID -- COMMAND [ARGS...]`. Remove `entrypoints`,
  `default_dispatch`, and `--dispatch`; invoke project scripts directly
  ([issue #90](https://github.com/SwissDataScienceCenter/hiraku/issues/90),
  [PR #112](https://github.com/SwissDataScienceCenter/hiraku/pull/112)).

- MLflow leaderboards use standard tracking URI and experiment environment
  variables when explicit settings are absent, preserving existing defaults.

- Named leaderboards in `hiraku/leaderboards/<name>.yml` define sources and
  optional metrics, with `hiraku leaderboard` commands for managing definitions.
- Run queries list attributed runs by default; `--include-unattributed` includes
  all source runs. `--leaderboard` selects a definition and `--record` filters
  attribution. Parameters are discovered automatically.
- Training code or its environment configures logging destinations.

- Render human-readable CLI tables with Rich; JSON output remains available for programmatic use ([PR #112](https://github.com/SwissDataScienceCenter/hiraku/pull/112)).
- Separate dashboard performance and trade-off plots into independently placeable components and named cells with their own controls, removing the analysis mode selector.
- Exclude generated Marimo and Studio session/build caches from distribution archives.
- Extract reusable dashboard widgets and rendering into `hiraku.dashboard.components`, keeping notebook cells focused on data and reactive selection wiring.
- Move dashboard branding and layout into the Studio HTML, CSS, and assets, leaving notebook cells for data and interactive outputs.
- `hiraku dashboard` automatically selects a free port when `--port` is omitted, in both view and edit modes.
- The dashboard is served through a [marimo-studio](https://marimo-team.github.io/marimo-studio/) view with the same layout, and `hiraku dashboard --edit` opens the copy in marimo-studio. `--init` also copies the view to `hiraku/dashboard/studio/`. The `dashboard` extra now installs `marimo-studio`.
- **Breaking:** `hiraku init` initializes a project independently of Git. Add `--git` to initialize the code repository explicitly.
- **Breaking:** `hiraku example create NAME [PATH]` copies a bundled example into `./NAME` by default without Git history or network access. Use `--with-results`, `--with-generator`, and `--generate` with `example create`. `hiraku init [PATH]` targets the current or explicit directory, and repeating plain initialization leaves the project unchanged ([issue #104](https://github.com/SwissDataScienceCenter/hiraku/issues/104)).
- **Breaking:** Store all research material as `Record` objects under `hiraku/records/`; use `type: context` for supporting context. Remove `hiraku.context`, `Context`, the `hiraku context` CLI group, context-specific MCP tools and resources, and the separate `hiraku/contexts/` directory ([issue #133](https://github.com/SwissDataScienceCenter/hiraku/issues/133)).

### Added

- Document submitting Slurm jobs through PSI/J's native `slurm` executor,
  including scheduler options passed with `slurm.*` custom attributes.
- Add native PSI/J execution with `prepare_job()` for research attribution,
  `run_local()` for foreground output, CLI `hiraku job submit/list/status`, and
  MCP `job_submit` / `job_list` / `job_status`. Project history stores serialized
  PSI/J specifications, executor targets, and native IDs under the original PSI/J job
  ID. Executors own execution and status; `Submission` explicitly converts,
  attaches, syncs, and saves metadata. Status queries refresh through PSI/J
  without waiting for completion. Lists refresh nonfinal jobs and retain saved
  final outcomes. Failed list observations return `UNKNOWN` with an error while
  preserving history. `observed_at` records when a snapshot was captured.
- Add `--launcher NAME` to `hiraku run` and `hiraku job submit`, with an equivalent
  MCP `job_submit` parameter. Registered PSI/J launcher names pass directly into
  the native job specification and submission history.
- Add CLI `--custom-attribute KEY VALUE` and MCP `custom_attributes` for
  executor-specific PSI/J custom attributes.
- Add the `local-detached` PSI/J executor with durable results and output,
  including final-status retrieval after the submitting process exits. The
  executor starts a detached Python supervisor to run the selected PSI/J launcher
  and save its outcome; attachments use per-job observers. Add a
  `renku` executor using the Renku data services API, OAuth2 device login,
  existing session launchers, final-state observation, and cancellation through
  session hibernation.
  Renku process exit codes are unavailable; logs and control remain with Renku.
  Foreground `hiraku run` streams output through temporary files and returns
  the process exit code.

- Add `hiraku example list` to list bundled examples with their descriptions.
- Register project-local MCP servers during `hiraku init`, using `uv run hiraku`
  when the project contains `uv.lock`, otherwise `hiraku`. Install bundled skills
  under `.agents/skills` and `.claude/skills` without adding client hooks.
- Add `Run.duration`, a run's wall-clock length in seconds. The CSV backend reads an optional `duration` column, MLflow uses the run's end minus start time, and W&B its `_runtime` summary. `hiraku leaderboard runs` shows it, and the dashboard adds a Duration column, run detail and plot tooltip next to the start time.
- Add optional, collapsible dashboard record navigation sharing selection with the run table and highlighting a selected run's owning record.
- Add a dashboard Markdown viewer for records with expandable front matter, and show the selected record's Markdown or a selected run's owning record in the dashboard.
- Add optional dashboard Markdown editing with explicit Save/Cancel, session-local drafts, and detection of external record changes before saving.
- Add `Record.save_markdown()` for saving a replacement body with metadata preserved and checking for external changes, independent of dashboard dependencies.
- Add the local stdio MCP server (`hiraku mcp`) with record CRUD, attributed launches, run queries, source resources, and an evidence-based proposal prompt; `hiraku init` registers it for Claude Code and Codex ([issue #80](https://github.com/SwissDataScienceCenter/hiraku/issues/80)).
- Add project-defined LangGraph workflows under `hiraku/workflows/<id>/`, with a
  `workflow.py` graph and optional `workflow.md` instructions. Import APIs from
  `hiraku.workflows.langgraph`.
- Persist workflow state through LangGraph's SQLite checkpointer. Resume using
  the workflow session ID and current checkpoint ID; reject stale checkpoints
  and serialize resumes across local processes.
- Add `hiraku workflow list|graph|start|status|resume` and MCP workflow tools.
  `workflow_approval` gathers approval for an interrupt before resuming it.
- Add the bundled `collaborative` workflow and the count-steps, greet, scheduler,
  and autoresearch example graphs.
- Record bodies are `hiraku.markdown.Markdown` strings that select a section by its heading line, e.g. `record.markdown.section("## Change")`, including its subsections. Headings are found with a CommonMark parser (the new `markdown-it-py` dependency), so headings in code blocks, lists, and block quotes do not start sections.
- Add a trade-off view for comparing two metrics across project runs in Marimo Studio and highlighting runs with strong trade-offs.
- Add `hiraku dashboard`, a local read-only dashboard with a record and run tree, run details with a link to the tracking backend, and a performance plot of runs in execution order. Install it with the `dashboard` extra.
- Add `hiraku dashboard --init` to copy the dashboard notebook into the project, `--edit` to open that copy in the marimo editor, and `--include-code` to show the notebook code in the read-only dashboard. The notebook exposes runs and records, with their place in the tree, as the Arrow-backed `runs_df` and `records_df` DataFrames.
- Add the MLflow `ui_url` option; MLflow runs now link to the MLflow web UI, also when `tracking_uri` is a credential-free HTTP(S) server.

### Fixed

- The dashboard's trade-off plot no longer fails with an internal error when a run lacks a metric (e.g. a crashed run) or a metric is an integer, as with pandas 3, where the run table's columns are Arrow-backed.
- The Heart Disease and minimal example writers and the autoresearch dispatcher now record when a run started instead of when it finished in `timestamp`, which the dashboard shows as the start time, and write the new `duration` column. **Breaking:** their CSV header gains `duration`, so append to a new `runs.csv` after updating them.
- Embed dashboard logos in the HTML entry document so Studio can build the branded view without rejecting local image references.
- Fix CLI table output crashing when there are no rows
  ([issue #96](https://github.com/SwissDataScienceCenter/hiraku/issues/96)).
- Read every page of MLflow experiments and runs; projects with more than 1,000 runs previously lost the rest.

### Removed

- **Breaking:** Remove `BaseRecord`; all research material uses `Record`, including task, experiment, context, and literature records.
- **Breaking:** Remove Programs and Actions, including `hiraku program`, `hiraku action`, `hiraku.agent.Program`, `hiraku.agent.Action`, their `hiraku://programs` and `hiraku://actions` MCP resources, and their bundled resources.
- **Breaking:** Remove the `default_program` configuration key. Unknown project
  configuration keys are rejected.
- **Breaking:** Remove unused `GitRepository` branch-management methods, `head`, `is_ancestor`, `parent_records`, and `child_records`, and the project-level branch-name template. Use Git directly for branch operations; record-local `branch` metadata is unchanged.
- **Breaking:** Remove the `hiraku runs list` subcommand; use
  `hiraku leaderboard runs` ([issue #66](https://github.com/SwissDataScienceCenter/hiraku/issues/66)).

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

- Named leaderboards in `hiraku/leaderboards/<name>.yml` define sources and
  optional metrics, with `hiraku leaderboard` commands for managing definitions.
- Run queries include all source runs; `--leaderboard` selects a definition and
  `--record` filters attribution. Parameters are discovered automatically.
- Training code or its environment configures logging destinations.

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

- Named leaderboards in `hiraku/leaderboards/<name>.yml` define sources and
  optional metrics, with `hiraku leaderboard` commands for managing definitions.
- Run queries include all source runs; `--leaderboard` selects a definition and
  `--record` filters attribution. Parameters are discovered automatically.
- Training code or its environment configures logging destinations.

- Replace the initial proof-of-concept data model with validated Pydantic schemas and a generic tracking-backend interface.
- Expand documentation with concepts, guides, examples, and custom-backend instructions.

### Removed

- Remove obsolete implementation plans and draft agent documentation.
