# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Add `--interactive` dashboard mode with authenticated record creation and background entrypoint launches, including launch preview, job status, bounded launch logs, and one completion refresh.
- Add subtree run scope, an interactive record graph, adapter-owned MLflow run links, and a composed dashboard workspace with breadcrumbs and source coverage.
- Add `ui_url` configuration for MLflow backends so run links can point at an explicit deployment UI.
- Add initial dashboard scope selection with `--scope source`, and save learning-curve images, epoch CSVs, and final-results JSON in the Digits MLflow example.
- Add `hiraku dashboard`, a local view-only marimo application for browsing records, inspecting provenance, sorting runs, and switching between progress and Pareto plots ([issue #78](https://github.com/SwissDataScienceCenter/hiraku/issues/78)).
- Add a read-only dashboard query layer that never mutates records and queries each selected tracking source once per refresh.
- Paginate MLflow run search so the dashboard does not silently truncate source runs at the MLflow page size.

### Fixed

- Share interactive dashboard jobs across browser sessions, enforce reviewed launch configuration and worktree commit requirements, and safely display live launch logs while preserving read-only controls.

- Render record graph labels and controls correctly in the browser, preserve graph search and zoom during navigation, and reveal selected records' ancestors.
- Check demo ports before training and support `--reopen` to restart retained demos without retraining, including MLflow links on custom ports.
- Keep historical attributed runs visible in dashboard source scope even when their record files are missing.
- Start the dashboard demo without automated tests by default; use `--run-tests` to enable them.
- Prefer declared research metrics for plot defaults, show larger charts and markers, and open the Digits demo in comparison scope and MLflow's Model training view.
- Open the Digits demo on the `digits` record in subtree scope and derive its MLflow `ui_url` from `--mlflow-port`.
- Make the dashboard a reactive marimo notebook with working record/run selection, cohort filtering, plot controls, and refresh status.
- Preserve source-qualified selections and cached source queries; sort numeric metrics and namespaced parameters correctly.
- Keep unavailable tracking sources distinguishable from empty results, retain labelled stale snapshots, and require explicit unknown Pareto directions.
- Protect local MLflow databases from initialization and migrations while browsing; include storage identity in run keys and paginate experiment discovery.

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
