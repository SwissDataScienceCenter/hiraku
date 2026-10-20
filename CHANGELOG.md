# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Breaking:** `hiraku init` no longer requires Git or a repository root; remove the obsolete `--ignore-git` flag. Add `--git` to initialize the code repository explicitly.
- **Breaking:** `hiraku init --example NAME` now targets `./NAME`; pass `.` to initialize the current directory. Explicit paths always take precedence, and repeating plain `hiraku init` leaves an initialized project unchanged ([issue #104](https://github.com/SwissDataScienceCenter/hiraku/issues/104)).
- **Breaking:** `hiraku runs` and `Record.runs()` now list only runs attributed to the selected record (the active record, or `--record ID`). Previously they returned every attributed run from the record's tracking source ([issue #66](https://github.com/SwissDataScienceCenter/hiraku/issues/66)).

### Added

- Add `hiraku git setup ignore|repo|submodule` and `hiraku git status [--json]` for optional metadata versioning after initialization. Submodule setup registers an existing metadata repository with a commit and records `--url` without network access; setup never clones, merges, commits, or pushes.
- Record bodies are `hiraku.markdown.Markdown` strings that select a section by its heading line, e.g. `record.markdown.section("## Change")`, including its subsections. Headings are found with a CommonMark parser (the new `markdown-it-py` dependency), so headings in code blocks, lists, and block quotes do not start sections.
- Add `Record.resolve_tracking_backend()` to build the tracking backend inherited by a record; `TrackingBackend.list_runs()` now lists all attributed runs when `record_id` is omitted.
- Add `hiraku runs --all` to list all attributed runs from the tracking source resolved for a record ([issue #66](https://github.com/SwissDataScienceCenter/hiraku/issues/66)).
- Add `hiraku dashboard`, a local read-only dashboard with a record and run tree, run details with a link to the tracking backend, and a performance plot of runs in execution order. Install it with the `dashboard` extra.
- Add `hiraku dashboard --init` to copy the dashboard notebook into the project, `--edit` to open that copy in the marimo editor, and `--include-code` to show the notebook code in the read-only dashboard. The notebook exposes runs and records, with their place in the tree, as the Arrow-backed `runs_df` and `records_df` DataFrames.
- Add the MLflow `ui_url` option; MLflow runs now link to the MLflow web UI, also when `tracking_uri` is a credential-free HTTP(S) server.

### Fixed

- Fix `hiraku record list`, `context list`, `program list`, `action list` (and other table output) crashing when there are no rows ([issue #96](https://github.com/SwissDataScienceCenter/hiraku/issues/96)).
- Read every page of MLflow experiments and runs; projects with more than 1,000 runs previously lost the rest.

### Removed

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
