# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Add project initialization from bundled templates, including the minimal and
  heart-disease examples ([PR #35](https://github.com/SwissDataScienceCenter/hiraku/pull/35)).
- Add project-local agent skill, program, action, and dispatcher resources during
  `hiraku init`.
- Add pre-commit hooks for Ruff formatting and linting ([issue #18](https://github.com/SwissDataScienceCenter/hiraku/issues/18), [PR #23](https://github.com/SwissDataScienceCenter/hiraku/pull/23)).
- Add semantic-versioning release configuration with `bump-my-version` ([issue #17](https://github.com/SwissDataScienceCenter/hiraku/issues/17)).

### Changed

- Rename the tracked-run query command to `hiraku runs`; `hiraku runs list` is
  also accepted.
- Improve `hiraku init` to support initializing a target directory and report
  paths relative to the initialized project.
- Improve the CLI and documentation for task and experiment workflows,
  entrypoints, dispatching, and run inspection.
- Update the autoresearch example to use an upstream project with a Hiraku
  dispatcher wrapper ([PR #25](https://github.com/SwissDataScienceCenter/hiraku/pull/25)),
  and validate required system utilities ([issue #37](https://github.com/SwissDataScienceCenter/hiraku/issues/37), [PR #38](https://github.com/SwissDataScienceCenter/hiraku/pull/38)).

### Removed

- Remove the standalone autoresearch example implementation in favor of the
  upstream-based example ([PR #25](https://github.com/SwissDataScienceCenter/hiraku/pull/25)).

## [0.1.0] - 2026-09-10

### Added

- Add the initial Hiraku CLI for managing projects, tasks, experiments,
  entrypoints, launches, and tracked runs.
- Add CSV, MLflow, W&B, and custom tracking-backend support.
- Add project initialization and configuration through `hiraku init`.
- Add structured Python APIs and generated reference documentation.
- Add the launcher command preview with `--dry-run` ([PR #16](https://github.com/SwissDataScienceCenter/hiraku/pull/16)).
- Add the Iris/W&B, MLflow, and autoresearch examples.

### Changed

- Replace the initial proof-of-concept data model with validated Pydantic
  schemas and a generic tracking-backend interface.
- Expand documentation with concepts, guides, examples, and custom-backend
  instructions.

### Removed

- Remove obsolete implementation plans and draft agent documentation.
