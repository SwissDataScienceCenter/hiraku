# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Execute workflow definitions through `Session.run_start()` and `session.run_event()`. Session owns persistence and correlation; Workflow owns definitions and Instruction, with dependencies running Session → Workflow → Graph. `session.validate_workflow()` returns the concrete state for execution while preserving independent inspection and cancellation.
- Separate step execution into `Workflow.run()`, returning validated state and instruction from prepared inputs without session persistence. Session records the step and handles submission outcomes.
- Move `State` and transition validation into the graph abstraction. Register immutable `Node` definitions through `@node` and inspect them through `graph.nodes`. Rename the current-position field to `State.node` and `session.state.node`, including CLI/MCP output; sessions use format version 2 without migration.
- Type saved state as `State`, allowing extra fields to preserve workflow-specific data without importing project workflow code. Authored state subclasses also allow extra fields unless explicitly configured otherwise.
- Generate event input models through `EventArguments.from_function()`, keeping handler signature validation in the graph compiler.
- Simplify terminal workflow workers to write `HIRAKU_EVENT_FILE`; the parent validates and submits after successful exit. Remove worker MCP configuration and journals. Reject invalid submissions without automatic retries; retain not-ready submissions for explicit resume.
- Compile standalone decorated classes with `WorkflowGraph.from_class()`; graph compilation owns the State contract and does not import the workflow implementation. `Workflow.build_graph()` applies Instruction and reserved-handler-name checks after compilation.
- Declare workflow entry methods with `@start(dest=...)`, require argument models for every event, and make graph reachability an explicit `check_reachability()` check. Direct graph construction validates structure while trusting typed definitions.
- Use string metadata in workflow event `Annotated` inputs as MCP argument descriptions, while preserving optional Pydantic constraints.
- Infer Python workflow event arguments from annotated keyword-only parameters, including defaults and Pydantic constraints, instead of requiring explicit argument classes.
- Build immutable workflow graphs per instance through `build_graph()`. Decorator-based and explicit definitions share structural validation; graph handlers are public definition fields.

- Replace workflow authoring with typed `EventArguments` and `State` models, `@node` instructions, and `@event` transitions registered into an immutable `Workflow.graph`. Import the public API from `hiraku.workflows`.
- Workflow descriptions come from the optional `description` field, which defaults to `None`; class docstrings are not used as descriptions.
- Use `load_workflow(id, config=...)` for one workflow and `list_workflows(config=...)` for a list of loaded `Workflow` objects. Directory discovery and importing are internal; CLI and MCP format their own listings.
- Remove `Instruction.requires_user_response` and its protocol flag. Approval is expressed in workflow text; the interactive agent driver pauses when the agent exits without an event.
- Workflow instances bind to a project's configuration file; `Workflow.config` reloads it on access. Execution methods no longer accept configuration. Access prompt text through the inherited `markdown` field.
- **Breaking:** `hiraku runs` and `Record.runs()` now list only runs attributed to the selected record (the active record, or `--record ID`). Previously they returned every attributed run from the record's tracking source ([issue #66](https://github.com/SwissDataScienceCenter/hiraku/issues/66)).
- **Breaking:** Rename the `hiraku.agent` module to `hiraku.context`; import `Context`, `load_context`, and `list_contexts` from `hiraku.context`.

### Added

- Add the local stdio MCP server (`hiraku mcp`) with record and context CRUD, attributed launches, run queries, curated source resources, and an evidence-based proposal prompt; `hiraku init` registers it for Claude Code and Codex ([issue #80](https://github.com/SwissDataScienceCenter/hiraku/issues/80)).
- Add Python workflows: project-defined `hiraku/workflows/<id>/{workflow.py,workflow.md}` that guide a coding agent step by step, with persistent sessions under `hiraku/sessions/`, explicit events per instruction, retry-safe continuation, inspection, and cancellation. Each workflow is an MCP tool and an MCP prompt named after it; events go through the `workflow_send_event` tool, whose arguments each instruction describes. See the Workflows guide.
- Add workflow and session tools, workflow prompts, and workflow and session resources to the MCP server, with `hiraku.mcp.add_workflow_tools()`. They are fixed when the server starts.
- `Workflow` adds optional Markdown `description` and an instance-owned graph to `BaseRecord`. Start and ordinary events return the complete next state, including its node; declared destinations validate transitions and generate diagrams. Node handlers return stored `Instruction` snapshots. `Instruction.to_text()` renders them for agents.
- `hiraku workflow graph ID [--session ID]`, `workflow.graph.to_mermaid()`, and the MCP graph resource draw the declared node/event graph.
- Record bodies are `hiraku.markdown.Markdown` strings that select a section by its heading line, e.g. `record.markdown.section("## Change")`, including its subsections. Headings are found with a CommonMark parser (the new `markdown-it-py` dependency), so headings in code blocks, lists, and block quotes do not start sections. Workflows select their prompts the same way, `self.markdown.section("## Step")`.
- Every workflow requires `workflow.md`. An optional Python sidecar defines a state machine. Without one, `MarkdownWorkflow` infers argument types from YAML string, integer, float, and boolean defaults, renders the body, and issues one final instruction. The optional `description` field is supported in both forms. Greet and autoresearch are Markdown-only examples.
- Add the bundled `collaborative` workflow: draft, implement, launch, and analyze experiments step by step; the agent does each step, sends a revision event with the user's feedback to redo it, and gets the user's approval in the conversation before reporting it, and Hiraku records and checks it. The `implemented` event takes only the implementation commit. The autoresearch example ships the single-instruction `autoresearch`, which restores its previous program.
- `Session.run_start()` and `session.run_event()` return typed `EventResult` outcomes without instructions. `session.get_instruction()` reads the saved assignment. Conversational MCP attaches the next instruction; CLI event submission prints only the outcome. Use module-level `load_session` and `list_sessions`. Sessions use a new versioned format without migration.
- Add the `workflow-examples` example (`hiraku init --example workflow-examples`) with three workflows: `count-steps`, numbered steps with random tokens the agent must echo back, where the user starts each next step (`step`), to check that a coding harness follows workflow instructions and stops for the user; `greet`, a prompt-only workflow whose arguments fill placeholders; and `scheduler`, which creates experiment records with random priorities and lets the agent raise their counts, highest priority first, until each reaches a target.
- Add `hiraku workflow list|start|send-event|graph` and `hiraku session list|show|cancel`.
- Add `hiraku-agent`, whose workers write one event file per assignment for the parent to submit. Custom workers consume `HIRAKU_EVENT_FILE`; failed workers' files are not submitted. It runs workflow sessions from the terminal: `hiraku-agent WORKFLOW` starts or resumes a session and hands each instruction to a headless coding agent (`claude -p` by default), continuing after each accepted event until completion or a pause; `hiraku-agent WORKFLOW EVENT` sends an event as the user, and `--interactive` runs the agent in a conversation instead. The new `hiraku.agent` module implements it; contexts moved to `hiraku.context`.
- Add `Record.resolve_tracking_backend()` to build the tracking backend inherited by a record; `TrackingBackend.list_runs()` now lists all attributed runs when `record_id` is omitted.
- Add `hiraku runs --all` to list all attributed runs from the tracking source resolved for a record ([issue #66](https://github.com/SwissDataScienceCenter/hiraku/issues/66)).
- Add `hiraku dashboard`, a local read-only dashboard with a record and run tree, run details with a link to the tracking backend, and a performance plot of runs in execution order. Install it with the `dashboard` extra.
- Add `hiraku dashboard --init` to copy the dashboard notebook into the project, `--edit` to open that copy in the marimo editor, and `--include-code` to show the notebook code in the read-only dashboard. The notebook exposes runs and records, with their place in the tree, as the Arrow-backed `runs_df` and `records_df` DataFrames.
- Add the MLflow `ui_url` option; MLflow runs now link to the MLflow web UI, also when `tracking_uri` is a credential-free HTTP(S) server.

### Fixed

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
