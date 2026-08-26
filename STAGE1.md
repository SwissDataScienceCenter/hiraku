# Stage 1 implementation plan

## Objective

Deliver the minimal working Hiraku proof of concept: an installable CLI and repository-local agent skill that support the complete task–experiment–run lifecycle in both guided and bounded-autonomous workflows.

Stage 1 is successful when the smoke and heart-disease examples can execute the core loop end to end, and a human can inspect the task definition, experiment intent, exact source provenance, launches, tracked results, conclusions, and decisions. Improving a metric is not required for success.

## Guiding constraints

- Keep Hiraku a thin coordination and metadata layer around Git, executable project code, and a tracking backend.
- Use tasks, experiments, and runs as the three principal concepts.
- Treat Git branches and commits as implementation provenance, not as replacements for Hiraku records.
- Bind each reproducible experiment to one exact source commit.
- Permit working-tree launches for debugging, but mark them as non-reproducible.
- Keep experiment records directly editable and independent of whether the user commits them.
- Do not introduce a launch registry, scheduler, tracking SDK, dashboard, or automatic enforcement of agent-program permissions.
- Ensure every configured or user-supplied path remains inside the project root.
- Make human-readable CLI output useful interactively and provide stable JSON output for agents.

## Delivery sequence

### 1. Establish the package and domain model

Create the installable Python package and `hiraku` command, declaring all runtime and development dependencies for use through `uv`.

Define internal models and validation for:

- project configuration;
- task front matter;
- experiment records;
- task launch configuration and named entrypoints; and
- table-backed run rows.

Define shared error types and CLI exit behavior for invalid configuration, missing objects, conflicting selectors, unsafe paths, malformed records, Git failures, and child-process failures.

Keep parsing, validation, repository discovery, Git operations, launching, tracking, and presentation as separate modules so command handlers remain orchestration code rather than the source of business rules.

Acceptance criteria:

- `uv sync`, the packaged CLI, and `uv run hiraku --help` work on the supported Python versions.
- Domain objects can be parsed and validated without invoking CLI commands.
- User-facing errors are concise and do not normally expose tracebacks.

### 2. Implement project discovery and configuration

Implement repository-root discovery and loading of `.hiraku.yml` with the Stage 1 configuration:

```yaml
tasks_dir: tasks
templates_dir: .hiraku/templates
default_template: guided
tracking_backend: table
```

Resolve all configured paths relative to the repository root. Reject absolute paths, traversal outside the root, unsupported tracking backends, invalid task directories, and configuration values of the wrong type.

Acceptance criteria:

- Every command operates from the repository root or a descendant directory.
- The resolved task, template, launch, working-directory, and tracking-table paths cannot escape the repository.
- Missing, malformed, and unsupported configuration produces deterministic errors.

### 3. Implement `hiraku init`

Initialize a repository with:

```text
.hiraku.yml
tasks/
.hiraku/templates/guided/{task.md,program.md,launch.yml}
.hiraku/templates/auto/{task.md,program.md,launch.yml}
.agents/skills/hiraku/
```

Ship complete guided and auto template bundles as package resources. Ship a repository-local Hiraku skill that routes agents to focused workflows for task refinement, program execution, experiment management, launching, analysis, and bounded autonomous iteration.

Define safe re-initialization behavior: create missing managed files and directories, but do not silently overwrite customized project files.

Acceptance criteria:

- Initialization produces a usable project from a Git repository.
- Both template bundles contain all three required task files.
- The installed skill is complete and uses the generated task's `program.md` as the source of workflow permissions and checkpoints.
- Re-running initialization is safe and reports collisions or preserved files clearly.

### 4. Implement task creation and inspection

Implement:

```text
hiraku task create <task-id> [--template <name>] [--branch <branch>]
hiraku task [--json] [--verbose]
hiraku task show <task-id> [--json] [--verbose]
hiraku task entrypoints [task-id] [--json]
```

For creation:

1. validate the task ID and ensure its directory does not already exist;
2. resolve the selected complete template bundle;
3. associate the task with the requested branch, defaulting to `hiraku/task/<task-id>`, without switching branches;
4. copy the bundle to `tasks/<task-id>/`;
5. populate task front matter with at least `id`, `branch`, and one-line `goal`; and
6. preserve the copied body for detailed goal, plan, evaluation protocol, findings, conclusions, and decisions.

Creation must not require a clean worktree, switch branches, or commit changes automatically. It must print instructions for switching to the task branch and must not retain the source template name as task metadata. The Hiraku skill checks the branch before implementation work and switches only when directed by the user, except that it may switch automatically when the working tree is clean and doing so is consistent with the user's instructions.

Implement common task resolution with the following precedence and conflict checks:

- an explicit `[task:]object` selector;
- `--task <task-id>` where supported; and
- the current Hiraku task branch.

Acceptance criteria:

- Default, auto, custom-template, and custom-branch creation work.
- `hiraku task` shows the current task and is equivalent to `task show` with the inferred task ID.
- Task display returns compact front-matter metadata in human-readable and JSON forms; `--verbose` additionally includes the Markdown body.
- `task entrypoints` lists the full structured definitions from the selected task's current `launch.yml` in JSON and a compact human-readable table.
- Conflicting task selectors fail rather than choosing one silently.
- Stage 1 does not expose `task list`.

### 5. Implement the file-backed experiment registry

Support YAML records and Markdown records with YAML front matter under:

```text
tasks/<task-id>/experiments/<experiment-id>.yml
tasks/<task-id>/experiments/<experiment-id>.md
```

Normalize both formats to the same schema:

```yaml
id:
task_id:
summary:
hypothesis:
status:
decision:
commit:
entrypoints: []
conclusion:
created_at: "<UTC ISO-8601 timestamp>"
updated_at: "<UTC ISO-8601 timestamp>"
description: # optional
```

Validate:

- record ID and filename agreement;
- stored `task_id` and containing task agreement;
- uniqueness across `.yml` and `.md`;
- rejection of unknown structured fields;
- status in `proposed`, `active`, `evaluated`, or `abandoned`;
- decision in `pending`, `accepted`, `rejected`, or `inconclusive`;
- UTC ISO-8601 `created_at` and `updated_at` timestamps;
- a resolvable exact commit when binding;
- at least one entrypoint before a reproducible experiment launch.

Stage 1 validates individual fields but deliberately defers cross-field invariants and lifecycle-transition rules among `status`, `decision`, and `conclusion`.

Implement consistently for both formats:

```text
hiraku experiment create [task:]<experiment> [--summary ...] [--format yaml|markdown]
hiraku experiment list [task] [--json]
hiraku experiment show [task:]<experiment> [--json]
hiraku experiment set [task:]<experiment> [--commit <ref>] [--summary ...] [--hypothesis ...] [...]
hiraku experiment delete [task:]<experiment>
hiraku exp list [task]
```

`hiraku exp` is an exact alias for `hiraku experiment`. Creation records equal `created_at` and `updated_at` values in UTC. Every successful `set` preserves `created_at` and refreshes `updated_at`. When `set --commit` is used, it resolves a Git ref to an exact SHA and stores the SHA. The field is not immutable; a later `set --commit` may replace it and inspection must reveal run/record commit mismatches. Other explicit `set` options update the corresponding mutable fields without changing unspecified fields.

Deletion affects only the local experiment record. Hiraku does not automatically ignore, commit, revert, or reset experiment implementation changes.

Acceptance criteria:

- YAML and Markdown operations have equivalent behavior.
- Directly edited valid records remain fully usable.
- Duplicate extensions, malformed front matter, invalid enums, unknown fields, and task mismatches are rejected.
- Creation initializes every core field deterministically.
- Listing without an explicit task uses the current task.
- Human-readable experiment listings include `hypothesis`.

### 6. Implement launch configuration and argument resolution

Parse each task's `launch.yml` as a map of named entrypoints. Each entrypoint defines:

- an argv-style command;
- default named arguments;
- a safe repository-relative working directory; and
- optional environment variables.

Experiments store an ordered list of concrete invocation strings, for example:

```yaml
entrypoints:
  - train
  - train --regularization 0.1
  - evaluate --split validation
```

Tokenize each string with a documented shell-like quoting grammar, but never execute it through a shell. The first token must name an entrypoint from `launch.yml`; remaining tokens are appended after that entrypoint's fully resolved command and arguments. Shell operators, substitutions, redirects, and environment assignments have no special behavior. Reject empty invocations, malformed quoting, and exact duplicates while preserving list order.

For an experiment-associated launch, normalize the task-qualified CLI entrypoint to its local name and require the entrypoint plus trailing CLI tokens to exactly match one declared invocation. Repeated-option precedence is left to the child CLI. Experiment invocations cannot structurally remove task-level defaults; projects needing different defaults define another named entrypoint.

Validate task-level commands as arrays and execute them directly without a shell. Define deterministic serialization rules for structured `launch.yml` arguments and append the matched experiment invocation tokens afterward.

Acceptance criteria:

- Commands, arguments, working directories, and environments resolve identically for working-tree and committed launches.
- Experiment invocation strings are tokenized deterministically and matched exactly without invoking a shell.
- No launch configuration can escape the repository or invoke a shell implicitly.
- Missing task, entrypoint, experiment selection, or required experiment entrypoint fails before execution.

### 7. Implement working-tree and reproducible launching

Implement:

```text
hiraku launch [--commit <ref>] [--experiment <id>] [task:]<entrypoint> [arguments...]
```

Working-tree launch behavior:

- execute directly from the active checkout;
- allow associated or unassociated launches;
- inject an empty source commit; and
- treat the result as non-reproducible.

Committed launch behavior:

1. resolve the requested ref to an exact SHA;
2. if an experiment is selected, require its bound commit and require any explicit `--commit` to resolve to the same SHA;
3. create a temporary detached Git worktree at that SHA;
4. load and resolve `launch.yml` and the entrypoint from the selected source snapshot while targeting the original project's fixed `tasks/<task-id>/runs.csv` table;
5. execute the child directly with the integration environment;
6. propagate its exit status; and
7. remove the temporary worktree without changing the active checkout.

Inject at least:

```text
HIRAKU_TASK_ID
HIRAKU_EXPERIMENT_ID
HIRAKU_SOURCE_COMMIT
HIRAKU_TRACKING_BACKEND
HIRAKU_TABLE_PATH
```

The launcher must not require the child to create a tracking row and must not synthesize run records itself.

Acceptance criteria:

- An exact committed snapshot executes even when the active checkout has unrelated changes.
- Experiment launches always execute their bound commit.
- Explicit commit mismatches are rejected before execution.
- Child exit codes are preserved.
- Temporary worktrees are cleaned up on success, child failure, and interruption where practicable.
- The active branch and checkout remain unchanged.

### 8. Implement the CSV/TSV tracking backend

Use one CSV table per task at the implicit path `tasks/<task-id>/runs.csv`. Stage 1 has no task-level tracking path setting. The backend interface may retain TSV parsing support for future use, but PoC launches always inject the fixed CSV destination.

Require these provenance columns:

```text
run_id
task_id
experiment_id
commit
created_at
```

The run producer or tracking backend records `created_at` as a UTC ISO-8601 timestamp when it writes the row; the launcher does not create rows or timestamps. Preserve all source columns and string values. Interpret an empty `experiment_id` as an unassociated run and an empty `commit` as a working-tree, non-reproducible run.

Validate table structure and enrich records in memory without rewriting the user's table.

Acceptance criteria:

- Both CSV and TSV tables load correctly.
- Arbitrary metric and metadata columns survive human and JSON presentation.
- Missing required columns, malformed rows, and task provenance mismatches are reported clearly.
- Empty valid tables and absent tables have defined, non-crashing behavior.

### 9. Implement experiment and run inspection

Implement:

```text
hiraku experiment list [task] [--json]
hiraku runs [task] [--sort <column> (--ascending|--descending)] [--json]
```

Support views for one explicitly selected task or the task inferred from the current task branch. Do not implement project-wide or cross-task listing. Experiment listings combine local records with derived run data, metrics, and Git-parent information; derived data is never copied back into the record merely for display.

Run listing behavior:

- preserve table order when no sort is requested;
- require exactly one direction when sorting;
- sort numeric values numerically;
- retain rows with missing or non-numeric values after ranked rows;
- assign one-based `_rank` only to ranked JSON rows; and
- group human output by task and experiment where that improves readability.

Expose bound-commit versus run-commit mismatches in experiment and run inspection.

Acceptance criteria:

- Human and JSON output represent the same flattened run collection.
- Sorting is stable and deterministic.
- Missing and non-numeric metrics remain visible and unranked.
- Explicit-task and current-task inference behavior is covered by integration tests.

### 10. Author the guided and auto programs and Hiraku skill

The guided program must require explicit human approval before:

- proceeding beyond task scope, goal, and implementation planning;
- committing an experiment implementation;
- launching runs; and
- finalizing findings and conclusions.

The auto program must require approval of goal, scope, evaluation protocol, permitted commands, resource bounds, and stopping conditions before starting. Within those boundaries it may plan, create and update experiments, implement and commit changes, launch runs, analyze results, record conclusions, accept or reject changes, and choose the next in-scope iteration.

It must pause before changing approved boundaries. Neither program may merge into a protected branch without explicit human approval.

The repository-local skill should route requests to focused instructions and use CLI JSON output where structured inspection is needed.

Acceptance criteria:

- Both programs operate on the same task and experiment schemas.
- Their behavioral difference is expressed entirely through copied program instructions and approvals, not alternate data models.
- A reviewer can reconstruct each demo's research process from repository and tracking artifacts without relying on hidden agent state.

### 11. Build standalone examples

Create `examples/smoke` as a repository template with deterministic synthetic scores. It should demonstrate initialization, task creation, experiment creation and binding, working-tree and committed launches, run tracking, ranking, conclusion recording, and task synthesis without network access.

Create `examples/heart-disease` as a repository template comparing logistic regression and random forest on the checksum-pinned UCI Cleveland Heart Disease cohort. Define distinct named entrypoints for the two model families, a fixed split and evaluation protocol, dependencies, and table tracking.

Document the copy-out workflow so examples are initialized only after being copied outside the Hiraku checkout.

Acceptance criteria:

- Each example becomes a standalone Git repository after copying and `git init`.
- Entrypoints and paths resolve relative to the copied repository root.
- The smoke example exercises the complete lifecycle deterministically.
- The heart-disease example verifies its dataset checksum and compares both required model families under one evaluation protocol.
- Tests do not create nested Git repositories inside `examples/`.

### 12. Verification and release readiness

Build a layered test suite:

- unit tests for schemas, selectors, path safety, argument resolution, sorting, ranking, and format normalization;
- Git-focused tests for branch creation, commit binding, dirty worktrees, detached temporary worktrees, and cleanup;
- CLI integration tests for every Stage 1 command in YAML and Markdown modes;
- end-to-end tests that copy and initialize the examples and execute the core loop;
- golden or structural tests for generated configuration, templates, programs, and the local skill;
- failure-path tests for malformed records, selector conflicts, unsafe paths, commit mismatches, child failures, and malformed tables; and
- package-build and clean-environment installation tests.

Configure linting, formatting checks, unit/integration/end-to-end jobs, package building, and Python 3.14 coverage in CI.

Before declaring Stage 1 complete, run a manual guided demonstration and a bounded-auto demonstration, then verify that all artifacts needed for post-hoc review are present and understandable.

## Suggested implementation milestones

1. **Foundation:** packaging, models, validation, repository discovery, configuration, and `init`.
2. **Research records:** task creation/show and the complete YAML/Markdown experiment registry.
3. **Execution:** launch configuration, working-tree launch, exact-commit worktrees, and environment injection.
4. **Evidence:** CSV/TSV loading, run inspection, ranking, derived experiment information, and mismatch reporting.
5. **Agent workflows:** guided/auto programs and the repository-local skill.
6. **Proof:** smoke and heart-disease examples, complete automated verification, and CI/build hardening.

Each milestone should leave the CLI usable and tested; avoid postponing integration until the examples are built.

## Definition of done

Stage 1 is complete when:

- the package installs and exposes the documented Stage 1 CLI;
- `hiraku init` creates safe, complete project scaffolding;
- tasks and YAML/Markdown experiments can be created, inspected, edited, bound, and deleted;
- working-tree and exact-commit launches behave as specified without altering the active checkout;
- table-backed runs can be filtered, displayed, sorted, ranked, and consumed as JSON;
- guided and bounded-auto programs complete the same lifecycle under their respective approval rules;
- both examples execute the full loop from copied standalone repositories;
- all expected provenance and conclusions are available for human review;
- unit, integration, end-to-end, lint, build, and Python 3.14 CI checks pass; and
- no Stage 2, deferred, or explicitly out-of-scope feature is required for the demonstrations.
