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
- Add CI runs for pull requests and pushes to `main`, plus a manual GitHub
  Actions release workflow ([issue #42](https://github.com/SwissDataScienceCenter/hiraku/issues/42)).

### Changed

- Improve `hiraku init` to support initializing a target directory and report
  paths relative to the initialized project.
- Improve the CLI and documentation for task and experiment workflows,
  entrypoints, dispatching, and run inspection.
- Update the autoresearch example to use an upstream project with a Hiraku
  dispatcher wrapper ([PR #25](https://github.com/SwissDataScienceCenter/hiraku/pull/25)),
  and validate required system utilities ([issue #37](https://github.com/SwissDataScienceCenter/hiraku/issues/37), [PR #38](https://github.com/SwissDataScienceCenter/hiraku/pull/38)).
- Update version bumps so the release commit and tag are created only after the
  release commit passes CI ([issue #42](https://github.com/SwissDataScienceCenter/hiraku/issues/42)).
- Keep the editable package version in `uv.lock` synchronized during release
  preparation ([issue #42](https://github.com/SwissDataScienceCenter/hiraku/issues/42)).
- Make releases manual GitHub Actions workflows that validate the exact release
  contents before committing, tagging, and creating a GitHub Release ([issue #42](https://github.com/SwissDataScienceCenter/hiraku/issues/42)).

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
