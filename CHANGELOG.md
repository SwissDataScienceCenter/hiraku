# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Breaking:** `hiraku runs` and `Record.runs()` now list only runs attributed to the selected record (the active record, or `--record ID`). Previously they returned every attributed run from the record's tracking source ([issue #66](https://github.com/SwissDataScienceCenter/hiraku/issues/66)).

### Added

- Add Python workflows: project-defined `hiraku/workflows/<id>/{workflow.py,workflow.md}` that guide a coding agent step by step through typed MCP tools, with persistent sessions under `hiraku/sessions/`, explicit replies per instruction, retry-safe continuation, inspection, and cancellation. See the Workflows guide.
- Add the `hiraku mcp` stdio MCP server with workflow, session, record, and run tools, plus workflow, record, session, and context resources. `hiraku init` registers it for Claude Code (`.mcp.json`) and Codex (`.codex/config.toml`), and the `mcp.workflows` setting selects the exposed workflows.
- `Workflow` is a `BaseRecord`: an instance is the loaded `workflow.md`, whose front matter holds the workflow `id` (matching its directory) and typed settings declared on the class, and whose body holds the prompts. `@tool("name")` sets a tool name independent of the method name.
- Add Markdown-only workflows: a workflow directory with only `workflow.md` sends its whole body as one instruction, with a `start` that accepts a free-text request and a single `complete` reply.
- Add the bundled workflows: `autoresearch-python` (the autonomous autoresearch loop with checked evidence and keep/discard/crash decisions), `guided-research` (a collaborative loop in which the user chooses hypotheses, approves launches, and discusses results), and `autodataset`. The autoresearch example also ships the Markdown-only `autoresearch`, which restores its previous program.
- `Workflow.call()` runs a workflow's tools on its stored `Session`s; the session holds the pending `instruction_id`, so retries and late replies are safe. Replies may omit `session_id` while one session of the workflow is active. `hiraku.research` provides shared task, run, and evidence helpers.
- Add `hiraku workflow list|activate|deactivate|call` and `hiraku session list|show|cancel`.
- Add `hiraku.record.update_record()`, shared by the CLI and MCP server.
- Add `Record.resolve_tracking_backend()` to build the tracking backend inherited by a record; `TrackingBackend.list_runs()` now lists all attributed runs when `record_id` is omitted.
- Add `hiraku runs --all` to list all attributed runs from the tracking source resolved for a record ([issue #66](https://github.com/SwissDataScienceCenter/hiraku/issues/66)).

### Removed

- **Breaking:** Remove Programs and Actions, including `hiraku program`, `hiraku action`, `hiraku.agent.Program`, `hiraku.agent.Action`, and their bundled resources; use workflows instead. Contexts are unchanged. An existing Markdown program becomes a Markdown-only workflow: move it to `hiraku/workflows/<id>/workflow.md` and keep its `id` front matter.
- **Breaking:** Remove the `default_program` configuration key; `hiraku.yml` files that still contain it are rejected with a migration message. Start workflows explicitly through their `<workflow>_start` tool.
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
