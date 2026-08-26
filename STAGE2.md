# Stage 2 implementation plan

## Objective

Extend the Stage 1 research lifecycle with Git-backed experiment branching, GitHub and GitLab review integration, an MLflow tracking backend, and a structurally verifiable GPU autoresearch example.

Stage 2 should preserve the lightweight Hiraku model:

- tasks and experiments remain semantic Hiraku records;
- Git branches and commits remain implementation provenance and lineage;
- tracking backends remain responsible for run storage;
- normal Git remains responsible for conflict resolution, branch integration, and worktree orchestration; and
- pull requests and merge requests remain human review surfaces that Hiraku may create or update, but never approve or merge.

Stage 2 is complete when the same task and experiment inspection interface works with table and MLflow tracking, experiment branches support parallel and backtracked research histories, `task.md` can be published deterministically as a GitHub or GitLab review description, and the autoresearch example expresses its loop through Hiraku without requiring a GPU in CI.

## Revised Stage 1 decisions

Stage 2 intentionally revises the following Stage 1 decisions. These changes must be applied to `README.md`, implementation, templates, and Stage 1 regression tests before Stage 2 features build on them.

- **Canonical task branches.** A task branch is always derived from its ID as `hiraku/task/<task-id>`. The branch remains recorded in `task.md` for inspection, but `hiraku task create` no longer accepts an arbitrary `--branch` value.
- **Task review metadata.** Task front matter gains a required one-line `summary`, used as the pull-request or merge-request title. GitHub tasks may record `pr_id`; GitLab tasks may record `mr_id`. The field matching the configured provider identifies one pull request or merge request deterministically.
- **Recorded experiment branches.** The experiment schema gains an optional `branch` field. An empty value retains the Stage 1 linear workflow. A branched Stage 2 experiment records the branch selected when it is created.
- **File-registry limitation.** Experiment records remain local files. By default they are left uncommitted or committed directly to the task branch, even when implementation work occurs on an experiment branch. Hiraku documents this limitation rather than pretending the registry is synchronized across branches.
- **Canonical review document.** `task.md` becomes the complete review description, not merely an input to a separately generated document. It must synthesize experiment branches, runs, analysis, open questions, conclusions, and decisions.
- **Task changelog.** Every task template gains a final `## Changelog` section. Each reviewed update to `task.md` appends a concise changelog entry; review publication uses the file exactly as it stands.

These are deliberate compatibility changes. Existing task branches with custom names require a documented migration or a clear compatibility error; Hiraku must not silently rename or move them.

## Scope boundaries

Stage 2 includes:

- experiment branch creation and task inference from experiment branches;
- experiment lineage derived from the Git DAG;
- skill/program guidance for accepting an experiment into its task branch with standard Git;
- pull-request and merge-request creation and updating;
- table/MLflow backend abstraction and MLflow run inspection;
- a GPU-oriented autoresearch example; and
- corresponding updates to the CLI, repository-local skill, task programs, documentation, examples, packaging, and tests.

Stage 2 does not include:

- task-branch advancement, automatic conflict resolution, or branch merging when an experiment is accepted;
- automatic worktree creation or orchestration;
- approving or merging pull requests or merge requests;
- cross-task experiment or run listing;
- W&B or other additional tracking adapters;
- a lineage dashboard;
- enforced agent-program permissions;
- autonomous creation of task scopes; or
- GPU execution in automated tests.

## Delivery sequence

### 1. Stabilize Stage 1 as the compatibility baseline

Before adding Stage 2 behavior, turn the existing Stage 1 suite into an explicit regression boundary.

Verify and retain:

- existing configuration defaults and safe repository-relative path handling;
- task creation, current-task inference, compact and verbose task inspection, and entrypoint listing;
- YAML and Markdown experiment parity, timestamps, `hiraku exp`, and direct editing;
- working-tree and exact-commit launches;
- table-backed run validation, sorting, ranking, and commit-mismatch reporting;
- guided and bounded-auto programs; and
- copied smoke and heart-disease example workflows.

Add compatibility tests asserting that projects without Stage 2 configuration continue to behave exactly as Stage 1 table-backed projects.

Acceptance criteria:

- all existing Stage 1 tests pass unchanged or are changed only where the Stage 2 specification intentionally extends an interface;
- `.hiraku.yml` files that omit new optional settings retain Stage 1 behavior; and
- Stage 2 does not make GitHub, GitLab, MLflow, GPU, or network dependencies mandatory for table-only local use.

**HOWEVER**: If a Stage 1 decision is incompatible or makes Stage 2 implementation difficult, it may be changed with a clear explanation in the Stage 2 specification. In this case, raise the proposed redesign to the user for discussion and approval.

### 2. Extend Git and branch-domain services

Add reusable Git operations needed by experiment branches and review integration without embedding Git commands directly in CLI handlers.

Implement read-only operations for:

- validating branch and ref names;
- resolving local branches, remote-tracking branches, tags, and commits;
- determining branch ancestry and merge bases;
- finding the task and experiment encoded by a branch name;
- determining whether a branch is published and whether it has an upstream;
- reading configured remotes and their fetch/push URLs;
- computing commit lists and changed paths between a base and task branch; and
- determining branch ancestry and whether user-performed acceptance has integrated an experiment.

Implement narrowly scoped mutations for:

- creating an experiment branch at an exact resolved commit;
- switching to an existing or newly created experiment branch;
- pushing the task branch only when `hiraku review create --push` explicitly authorizes it.

Keep subprocess execution argv-based and non-interactive. The explicit `--push` flag is the only push authorization; there is no interactive confirmation. Preserve user changes and surface Git's refusal when switching would overwrite or conflict with a dirty worktree.

Acceptance criteria:

- Git failures are translated into concise Hiraku errors without hiding the original actionable detail;
- no operation silently discards commits, tracked changes, or untracked files;
- branch mutations identify their exact source and target refs before acting; and
- unit tests cover detached HEAD, unborn repositories, dirty worktrees, missing refs, invalid names, branch-name collisions, and non-ancestor histories.

### 3. Implement experiment-branch identity and task resolution

Adopt the Stage 2 branch namespace:

```text
hiraku/experiment/<task-id>/<experiment-id>
```

Extend current-task inference so commands executed on an experiment branch resolve `<task-id>`. Extend current-experiment inference where useful so the branch's `<experiment-id>` can be checked against an explicit selector rather than silently overridden.

Validate that:

- task and experiment IDs encoded in the branch use Hiraku's ID rules;
- the containing task and local experiment record exist where the command requires them;
- an explicit task or experiment selector agrees with the current experiment branch; and
- the recorded task branch equals the canonical ID-derived task branch.

Do not infer experiments merely from arbitrary Git ancestry; branch naming provides checkout identity while commit relationships provide lineage.

Acceptance criteria:

- `hiraku task`, task-local `hiraku exp` operations, `hiraku runs`, and launch commands resolve the task from an experiment branch;
- conflicting explicit and inferred selectors fail deterministically; and
- Stage 1 task-branch inference remains unchanged.

### 4. Extend `hiraku experiment create` with branch creation

Add:

```text
hiraku experiment create [task:]<experiment> --branch [--from <ref>]
hiraku exp create [task:]<experiment> --branch [--from <ref>]
```

The command should:

1. resolve the task and validate the new experiment ID;
2. create the experiment record with its `branch` field;
3. derive `hiraku/experiment/<task>/<experiment>` under the canonical naming policy;
4. resolve `--from`, defaulting to the task branch HEAD and otherwise accepting any branch or commit that resolves exactly;
5. create the branch at the resolved source commit;
6. attempt to switch the active checkout to the branch; and
7. report the record path, branch name, and resolved starting SHA.

The command must not create a worktree, commit files, bind the experiment commit, merge another branch, or modify tracking data. If the branch already exists, warn and attempt to switch to it without moving or resetting it. If switching fails, preserve the created/updated record and print the exact manual `git switch` instruction.

Acceptance criteria:

- default branching starts at task-branch HEAD;
- `--from` accepts another experiment branch, task branch, tag, or exact commit;
- creation works consistently through `experiment` and `exp`;
- dirty-worktree and existing-branch behavior is explicitly tested; and
- experiment listings derive parent and ancestor relationships from commits, not copied record fields.

### 5. Enrich experiment lineage inspection

Extend normalized experiment inspection with Git-derived lineage suitable for human output, JSON, and review descriptions.

Candidate derived fields include:

- bound commit;
- direct parent commit or commits;
- nearest bound parent experiment or experiments within the task;
- experiment branch name and local existence;
- whether the bound commit is contained in the task branch;
- divergence from the task branch; and
- run/record commit mismatches already provided by Stage 1.

Define a stable JSON schema for these fields. Store only the selected `branch` in the experiment record; lineage remains derived from Git.

The human `experiment list` table includes the recorded branch for every experiment. Prefix the branch with `*` when the exact bound commit is contained in the task branch and print a legend identifying the marker and task branch. Use `-` for an experiment without a recorded branch; it may still receive the marker when its bound commit is contained. Keep the explicit `contained_in_task_branch` Boolean in JSON instead of encoding the marker into structured data. An accepted experiment without the marker produces a warning. Squash merges and cherry-picks do not qualify because exact Git ancestry cannot prove that the bound implementation commit was integrated.

Acceptance criteria:

- linear, sibling, backtracked, and multi-parent histories produce deterministic lineage output;
- missing or deleted local branches do not erase lineage available from bound commits;
- unbound and missing commits are represented without crashing; and
- human listings include branches, mark task-branch containment compactly, and warn when accepted experiments are not contained;
- human listings remain readable while JSON retains the full structured lineage.

### 6. Document and verify experiment acceptance

Hiraku does not move, merge, reset, or switch the task branch when an experiment is accepted. The user—or the agent acting within its approved program—uses standard Git operations to advance the canonical task branch to the accepted experiment branch. Hiraku records the semantic decision through `hiraku experiment set ... --decision accepted` and provides branch and commit context, but Git integration remains external.

Update the skill and programs so they:

- verify implementation occurs on the experiment's recorded branch;
- inspect task/experiment ancestry before proposing acceptance;
- show the exact standard Git operation appropriate to the current history;
- require the existing guided or auto approval boundary before performing that Git operation;
- update `task.md` on the task branch with the accepted experiment, findings, and changelog; and
- never delete the experiment branch or push automatically.

Acceptance criteria:

- Hiraku can report whether an accepted experiment's bound commit is contained in the task branch;
- no Hiraku CLI command mutates the task branch as a side effect of setting `decision: accepted`;
- linear, divergent, dirty-worktree, and multi-worktree cases are documented as normal Git workflows; and
- acceptance remains distinct from approving or merging a remote review.

### 7. Define a review-provider abstraction

Create a small provider-neutral review model before adding GitHub and GitLab adapters.

The abstraction should cover:

- explicitly configured provider and selected remote;
- repository identity;
- base and head branches;
- review lookup by provider-specific identifier and discovery by head/base;
- create versus update behavior;
- title, description, and web URL;
- publication state of the head branch; and
- provider command execution and authentication failures.

Keep provider output parsing isolated from review-description generation. Prefer structured JSON emitted by `gh` and `glab`; do not scrape human-formatted terminal tables.

Acceptance criteria:

- provider-independent orchestration can be tested with fake adapters;
- no provider CLI is invoked for local-only commands;
- absence of `gh` or `glab` produces an actionable error only when that provider is needed; and
- command construction never invokes a shell.

### 8. Make `task.md` the deterministic review description

Update the task-review workflow so the agent or user synthesizes the complete review directly into `task.md`. Hiraku does not generate or maintain a second review file. Provider updates read the current `task.md` verbatim and fully replace the pull-request or merge-request description with it.

Include at least:

- task metadata, goal, scope, and evaluation protocol;
- experiment branches and a normalized reviewed synthesis of experiments and runs;
- analysis, findings, open questions, conclusions, and decisions; and
- a final `## Changelog` section whose newest concise entry is appended whenever the reviewed task document changes.

Task front matter must include a non-empty, one-line `summary`, which is the review title. With `review_provider: github`, it may also contain an opaque `pr_id`; with `review_provider: gitlab`, it may contain an opaque `mr_id`. Hiraku rejects `mr_id` for GitHub and `pr_id` for GitLab rather than silently using the wrong provider's identifier. Neither identifier is required before the first review is created. `goal` remains the scientific objective and is not overloaded as review UI text.

The skill should use structured Hiraku JSON to help update `task.md`, but the CLI does not silently rewrite scientific narrative. Validate and preview `task.md` before any push or provider mutation. Add golden-file tests for templates and provider payload tests proving that the file is transmitted verbatim.

Acceptance criteria:

- every task template ends in `## Changelog`;
- every task template includes `summary`; `pr_id` or `mr_id` is added when a review is created for the configured provider;
- review preparation exposes local experiment records, table/MLflow runs, and lineage to the skill for synthesis;
- provider adapters receive the exact same `task.md` content; and
- updating a review replaces the full remote description rather than appending duplicate content.

### 9. Implement `hiraku review create`

Add:

```text
hiraku review create [task] [--base <branch>] [--remote <name>] [--review <id>] [--push]
```

The command should:

1. resolve or infer the task;
2. resolve the canonical task branch as the review head, even when invoked from an experiment branch;
3. resolve `--base` or the remote repository's default branch;
4. select `--remote`, the configured remote, the branch remote, `origin`, or the sole remote in that precedence order;
5. load the provider explicitly from `.hiraku.yml`;
6. validate authentication and repository access;
7. read and print the complete `task.md` description locally;
8. detect whether the head branch is already published;
9. if unpublished and `--push` is absent, stop after the preview and print exact push/re-run instructions;
10. if `--push` is present, push only the exact task branch;
11. resolve the review identity from `--review`, then `task.md`'s provider-matching `pr_id` or `mr_id`; if both are present and disagree, fail without mutation, and reject an identifier field belonging to the other provider;
12. when an identifier is available, fetch that exact review and replace its title and entire description;
13. when no identifier is available, search for reviews with the same head and base, including closed reviews; if any match, warn, list every match with its identifier, state, and URL, make no provider mutation, and instruct the user to rerun with `--review <id>`; and
14. when no identifier and no matching review exist, require the canonical task branch to be checked out; otherwise stop before provider creation and print an exact switch-and-rerun instruction; and
15. from the checked-out task branch, create a new review, write its returned identifier as `pr_id` or `mr_id` in `task.md`, and update the remote description once more so it exactly matches the newly updated file.

The command must never approve, merge, close, or delete a review. JSON output should return provider, action (`created` or `updated`), review identifier, URL, base, head, and resolved SHAs.

Acceptance criteria:

- task inference works from either the task branch or an experiment branch without changing the active checkout;
- preview and updates selected by an explicit or recorded identifier work from experiment branches, but first-time creation stops before mutation unless the canonical task branch is checked out;
- unpublished branches are never pushed without `--push`;
- preview-only behavior is deterministic and performs no provider mutation;
- published branches are not pushed unnecessarily unless an explicit update is required;
- repeated invocation updates one existing review rather than creating duplicates;
- an explicit or provider-matching recorded identifier is authoritative, while unselected head/base matches—including closed reviews—are listed without guessing or mutation;
- newly created reviews persist `pr_id` or `mr_id` in `task.md` and end with the local file and remote description identical; and
- provider failures leave local task and experiment records untouched.

### 10. Implement GitHub and GitLab providers

GitHub adapter:

- use the provider selected in `.hiraku.yml` rather than URL heuristics;
- use authenticated `gh` commands with structured output;
- inspect publication and pull-request state;
- create or edit the pull request; and
- return the canonical pull-request URL.

GitLab adapter:

- support the configured remote, including self-managed GitLab hosts, without hostname inference;
- use authenticated `glab` commands with structured output;
- inspect publication and merge-request state;
- create or edit the merge request; and
- return the canonical merge-request URL.

Use subprocess-level contract tests with fake executables plus optional, separately gated live smoke tests. CI must not require provider credentials or mutate real repositories.

Acceptance criteria:

- HTTPS and SSH URLs for explicitly configured providers are tested;
- GitHub and GitLab generate equivalent normalized review content;
- authentication, missing executable, fork, remote, and API errors are actionable; and
- neither adapter exposes approve or merge operations.

### 11. Introduce a tracking-backend interface

Refactor the Stage 1 table implementation behind a backend contract before adding MLflow.

The contract should provide:

- backend configuration validation;
- launch-environment construction;
- task-scoped run retrieval;
- normalized provenance fields;
- arbitrary parameters, metrics, and metadata without losing source values;
- sorting/ranking inputs;
- experiment/run commit-mismatch detection; and
- backend-specific diagnostics.

Keep human and JSON presentation backend-neutral. The table adapter should continue to pass all Stage 1 tests after the refactor.

Acceptance criteria:

- CLI handlers do not branch on backend type beyond adapter selection;
- normalized run JSON has a documented stable core across table and MLflow;
- backend-specific data can be retained under a namespaced or documented extension field; and
- table projects require no MLflow installation.

### 12. Extend configuration for MLflow

Extend project configuration for review integration and MLflow:

```yaml
tracking_backend: mlflow
mlflow_tracking_db: mlflow.db  # optional; defaults to this project-root path
review_provider: github  # or gitlab
review_remote: origin    # optional
```

Extend task front matter with:

```yaml
mlflow_experiment: <experiment-name>
```

For the PoC, MLflow uses a local SQLite store at `<project-root>/mlflow.db` by default. The optional `mlflow_tracking_db` setting overrides it with another safe project-relative database path. Resolve the path against the original project root and construct an absolute `sqlite:///` URI so committed launches from temporary worktrees still write to the active project's store. Remote tracking servers, registry URIs, and authentication configuration remain out of scope. Validate the project/backend combination and require the task's MLflow experiment name when MLflow is selected.

SQLite replaces MLflow's maintenance-mode filesystem tracking backend, so Hiraku does not set `MLFLOW_ALLOW_FILE_STORE`. Production tracking-server guidance remains out of scope.

Acceptance criteria:

- unknown backends remain invalid;
- review provider is explicitly `github` or `gitlab` when review commands are used;
- the configured review remote is optional and may be overridden by CLI `--remote`;
- `mlflow_tracking_db` defaults to `mlflow.db`, must remain within the project root, and is valid only for the MLflow backend;
- table tasks reject or ignore MLflow-only fields according to a documented policy;
- MLflow tasks fail early when required configuration is absent; and
- configuration errors identify the exact project or task field involved.

### 13. Implement the MLflow tracking backend

Add MLflow run retrieval and normalization for one Hiraku task. Use MLflow's native run ID and start time for `run_id` and `created_at`; read Hiraku provenance from user-logged MLflow parameters:

```text
task_id
experiment_id
commit
```

Map MLflow parameters, metrics, tags, status, and timestamps into the normalized run representation without flattening collisions silently. Namespace them as `params`, `metrics`, and `tags` in JSON. Human output may flatten qualified names such as `metrics.val/loss`. For each metric key, use the value returned by MLflow run search: MLflow defines this as the value with the latest logged timestamp. Metric history is not part of Stage 2.

Task front matter may declare ordered evaluation metrics:

```yaml
metrics:
  - name: val/log_loss
    direction: minimize
  - name: val/accuracy
    direction: maximize
```

Names must be unique and directions must be `minimize` or `maximize`. The declaration is backend-neutral. It determines the default human metric columns, makes the first metric the default ranking metric, and supplies ranking direction. An explicit `--sort` without a direction is valid only for a declared metric; `--ascending` or `--descending` overrides its declaration.

The default human `hiraku runs` output is compact: abbreviated run and commit IDs, experiment, time, optional backend status, useful MLflow parameters, and selected metric columns. Do not print both nested and flattened backend data. Hide the redundant task ID, MLflow system tags, and false commit-mismatch values; emit a warning when mismatches exist. `--verbose` exposes complete flattened metadata. Human commit hashes use ten characters consistently across run and experiment views, while records and JSON preserve exact hashes.

Determine available metrics from the union across returned runs. MLflow metrics come only from MLflow's metric mapping. Since table files do not distinguish parameters from metrics, treat all non-provenance columns as result metrics. `--metrics <comma-separated-names>` selects and orders human metric columns. Otherwise use declared metrics, or alphabetical discovered metrics when none are declared. Limit an inferred undeclared default to six columns and tell the user to use `--metrics` or `--verbose` when more are available. Put an explicitly sorted metric first and leave missing metric cells blank.

Integrate with:

- `hiraku runs [task] [--all]` human and JSON output;
- numeric sorting and ranking;
- `hiraku experiment list/show` run enrichment;
- commit-mismatch reporting;
- `task.md` review synthesis and publication; and
- launch environment injection needed by user code to select the configured MLflow experiment.

Hiraku remains a tracking client, not a training SDK: the child process creates the MLflow run and records required provenance. A successful launch does not require a resulting run.

By default, MLflow listings include only runs whose `task_id`, `experiment_id`, and `commit` parameters are all present and whose `task_id` matches the selected task. They warn with a count of omitted invalid or unassociated runs. The MLflow-only `--all` option includes every run in the task's configured MLflow experiment, preserves missing provenance as unassociated data, and emits per-run warnings; runs without valid numeric sort values remain unranked. Table-backend behavior is unchanged, and table projects reject `--all` as an MLflow-only option.

Acceptance criteria:

- default listings exclude provenance-incomplete or wrong-task runs with a summary warning, while `--all` reveals them with precise warnings;
- missing local stores and empty MLflow experiments fail clearly;
- sorting and ranking match table-backend semantics;
- compact and verbose human tables avoid duplicated backend data, metric selection is deterministic, and JSON remains lossless;
- declared metrics drive default columns and direction while undeclared metric unions remain usable;
- MLflow is packaged as an optional Python extra and is imported only for MLflow projects; and
- integration tests use a temporary local SQLite MLflow store without external services.

### 14. Update launch provenance for backend neutrality

Replace table-specific assumptions in launch orchestration with backend-provided environment values.

For table tasks, retain:

```text
HIRAKU_TRACKING_BACKEND=table
HIRAKU_TABLE_PATH=<original checkout>/tasks/<task-id>/runs.csv
```

For MLflow tasks, inject the resolved project-root local tracking URI/path and configured MLflow experiment name using documented environment variables. Continue to inject task ID, experiment ID, and source commit for both backends so user code can log them as parameters.

Acceptance criteria:

- committed launches still load `launch.yml` from the selected source commit;
- tracking destinations still point to the active project's backend rather than a temporary worktree;
- no table-only variable is misleadingly populated for MLflow; and
- working-tree and exact-commit behavior remains otherwise identical.

### 15. Update the repository-local skill and programs

Extend the installed skill to route and explain:

- creating and switching experiment branches;
- checking branch identity before experiment implementation;
- deriving lineage and selecting a parent experiment;
- accepting an experiment under the resolved local Git policy;
- previewing reviews and creating or updating them with explicit `--push` authorization where publication is needed;
- inspecting table or MLflow runs through backend-neutral JSON; and
- running the bounded autoresearch loop.

Update guided and auto programs so:

- guided mode pauses before experiment-branch creation where it changes the active checkout, before acceptance, before pushing, and before creating/updating a review;
- auto mode may create and work on experiment branches only within approved Git and resource boundaries;
- neither mode approves or merges reviews; and
- changing tracking backend or the local MLflow store/experiment configuration is treated as a scope/configuration change requiring the program's normal approval.

Acceptance criteria:

- copied Stage 1 task programs remain valid;
- newly initialized templates include Stage 2 guidance without changing the shared data model unnecessarily; and
- skill tests verify routing and explicit safety boundaries without treating instructions as enforcement.

### 16. Add the autoresearch example

Create a standalone GPU-oriented repository template under `examples/` by copying and adapting the latest `karpathy/autoresearch` revision available when Stage 2 implementation begins. Record the exact upstream commit SHA and preserve its MIT license and attribution so the example remains reproducible after upstream changes.

The example should define:

- a task goal and fixed evaluation protocol;
- an auto program with permitted commands, GPU/time/resource bounds, stopping conditions, and protected-branch restrictions;
- deterministic named entrypoints;
- one experiment record per coherent iteration;
- experiment branches representing sibling, child, and accepted research directions;
- exact source-commit binding;
- table tracking by default, matching the upstream program's lightweight results log, plus an MLflow-compatible mode tested against the local backend;
- run comparison and next-iteration selection; and
- post-hoc task and review artifacts.

Document provenance and licensing for any copied or adapted upstream material. Avoid vendoring unnecessary model weights, datasets, environments, or generated run artifacts.

Acceptance criteria:

- the example can be copied and initialized as a standalone Git repository;
- structural tests validate configuration, task templates, programs, entrypoints, experiment conventions, and branch workflow without a GPU;
- a separate CPU mock entrypoint behaves like the reference workflow at the orchestration and output-contract level, and CI executes it without pretending to validate GPU model quality; and
- real GPU execution instructions identify dependencies, expected resources, stopping behavior, and artifacts.

### 17. Verification and release readiness

Extend the test suite with:

- unit tests for branch parsing, lineage inference, provider detection, `pr_id`/`mr_id` resolution and provider mismatches, task summary validation, changelog generation, normalized review content, MLflow mapping, and backend selection;
- Git integration tests for branched experiment creation, arbitrary `--from` refs, backtracking, siblings, dirty worktrees, existing branches, and ref preservation on failure;
- CLI integration tests for `experiment create --branch`, task inference from experiment branches, review preview/`--push`, and review JSON output;
- fake-`gh` and fake-`glab` contract tests for create/update by ID, `pr_id`/`mr_id` persistence, first-time creation from an experiment branch, head/base discovery without mutation, closed and multiple matches, authentication failures, unpublished branches, and duplicate prevention;
- local SQLite MLflow integration tests for default and overridden database paths, path safety, runs, metrics, default/`--all` filtering, warnings, ranking, enrichment, and mismatches;
- end-to-end table and MLflow task workflows;
- structural and dry-run autoresearch tests;
- packaging tests proving GitHub, GitLab, MLflow, and GPU integrations remain optional where intended; and
- lint, formatting, build, Python 3.11, and Python 3.14 CI coverage.

Run final manual demonstrations for:

1. sibling experiment branches and backtracking;
2. standard-Git experiment acceptance into a task branch, with Hiraku verifying the resulting containment;
3. GitHub review create then update using a disposable test repository;
4. GitLab review create then update using a disposable test repository;
5. equivalent task inspection through table and MLflow backends; and
6. the autoresearch structural/dry-run workflow.

Provider demonstrations must use explicitly designated disposable repositories and must never approve or merge the resulting reviews.

## Suggested milestones

1. **Stage 1 revisions:** canonical ID-derived task branches, experiment `branch` metadata, task review/changelog templates, and migration behavior.
2. **Branch foundation:** Git services, experiment branch parsing, task inference, and `experiment create --branch`.
3. **Lineage and acceptance guidance:** enriched DAG inspection and verification of user-managed Git integration.
4. **Review core:** `task.md` synthesis guidance, provider abstraction, preview behavior, and explicit `--push`.
5. **Provider adapters:** GitHub and GitLab create/update flows with fake and gated live tests.
6. **Backend abstraction:** table adapter extraction and stable normalized run schema.
7. **MLflow:** local configuration, optional extra, retrieval, ranking, enrichment, and review integration.
8. **Autoresearch:** pinned upstream GPU example, CPU mock validation, programs, and skill updates.
9. **Release hardening:** full regression suite, clean optional-dependency installs, builds, and manual demonstrations.

Each milestone should remain independently testable. Branching should not wait on provider integration, and `task.md` review publication should be testable without network access or installed provider CLIs.

## Definition of done

Stage 2 is complete when:

- branched experiments can be created with `experiment create --branch` from the task branch or any explicitly selected prior ref, and current-task inference works from them;
- lineage is derived consistently from Git for linear, sibling, and backtracked experiments;
- acceptance is performed with normal Git under skill/program guidance, and Hiraku reports whether the accepted commit is contained in the task branch without mutating it;
- `hiraku review create [task] [--base <branch>] [--remote <name>] [--review <id>] [--push]` previews or safely creates/updates GitHub pull requests and GitLab merge requests with persistent `pr_id`/`mr_id` identity;
- `task.md` is the exact review description, is fully replaced on update, and ends with an append-only reviewed changelog section;
- Hiraku never approves or merges reviews;
- table and MLflow projects expose equivalent task-scoped run inspection, ranking, experiment enrichment, and mismatch reporting;
- MLflow uses native run IDs/start times, user-logged provenance parameters, latest metric values, a project-root local store with a safe optional override, provenance-filtered defaults, `--all`, and an optional dependency extra;
- the pinned and attributed autoresearch adaptation represents the Hiraku loop with table and MLflow modes and passes CPU-mock validation;
- Stage 1 workflows remain compatible apart from the explicitly documented canonical-branch and task-template revisions; and
- unit, integration, end-to-end, lint, formatting, build, Python 3.11, and Python 3.14 checks pass.
