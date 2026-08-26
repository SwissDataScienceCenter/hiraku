# Hiraku

**Hiraku** is a toolkit for AI-assisted data science research.

It defines a workflow, conventions and meta-data tracking around standard data science and machine learning practices, linking task a scope and goals, documentation, experiments, execution runs, and results. A shared metadata record enables both guided, human–AI collaboration and autonomous research, with explicit boundaries and checkpoints for reviewing and verification. The toolkit provides deterministic support functions via a command-line interface (`hiraku`) and agent skills (`/hiraku`).

**Why Hiraku?**
In renku (連句), the traditional Japanese art of collaborative linked poetry, hiraku (平句) are the regular verses where the sequence develops, shifts, and explores new directions — making the name a natural metaphor for iterative, exploratory research within the Renku ecosystem.

> Treat the name as internal code name; suggestions are welcome :)


## The problem

In the experimental phase of a data science project, the problem, data, and objective are typically well defined, and the focus shifts to iterative model development (cf. [CRISP-ML](https://ml-ops.org/content/crisp-ml)). Data scientists typically cycle through a sequence of steps:

1. defining an experiment (e.g., a baseline or hypothesis),
2. implementation and debugging,
3. executing runs and tracking metrics,
4. analyzing and comparing results, and
5. iterating or concluding.

While standard tools support individual stages of this cycle (Git for version control, Weights & Biases or MLflow for experiment tracking, and notebooks for analysis, etc), the meta-level information that connects these stages is often only loosely documented.

This fragmentation creates inherent barrier for effective human-AI collaboration: ideas, experiments, hypotheses, implementation, results, and conclusions are not consistently linked or documented as part of a coherent research process. Without a lightweight but explicit protocol for capturing the research process, AI systems cannot reliably inspect prior work, understand why decisions were made, propose meaningful next steps, to effectively support data scientists through iterative experimentation.



## Hiraku core workflow: Tracking the task life-cycle

Hiraku addresses this gap by establishing conventions, workflows, and tools for AI-assisted data science research. A task defines a working scope and goal. Experiments are smaller units that link a task, hypothesis, code, execution entrypoints, and results. Runs record execution traces for a specified experiment and code commit. Hiraku ships complete agent instructions (`program.md`) for two common workflows:

* **Guided template**: A step-by-step program where the data scientist explicitly approves each checkpoint and reviews artifacts before proceeding.
* **Auto template**: An agentic, closed-loop program with explicit permissions, resource bounds, and stopping conditions under which the agent implements, runs, analyzes, and iterates, exposing all artifacts for post-hoc human verification.


Hiraku connects meta data at three levels:

- A **task** is the top-level unit, directly tied to an explicit goal. It corresponds roughly to a feature branch in git, and contains the sequence of experiments conducted toward that goal.
- An **experiment** is the key record connecting a task's research intent to one exact source commit and the launches and runs that evaluate it. It may be a small ablation or a broader coherent investigation, and remains cheap to create, update, review, or discard.
- A **run** is one execution trace of an experiment, with its configuration, provenance, and results. Several runs may evaluate the same experiment.

Tasks and experiments are Hiraku concepts, while branches and commits provide their implementation provenance. Each experiment refers to one exact source commit; relationships between those commits provide experiment lineage. Experiment branches within a task add support for parallel workflows, and tree branching. GitHub pull requests or GitLab merge requests provide a standard reviewing surface before a task is concluded and merged into main. A tracking backend (MLflow, CSV/TSV table, W&B, etc.) tracks runs and metrics.

Hiraku CLI (`hiraku`) and agent skill (`/hiraku`) support task creation, experiment implementation, task documentation, and task-local experiment and run comparison.

### Task record

Each task contains three directly managed files at its top level:
```text
tasks/<task-id>/
  task.md
  program.md
  launch.yml
  experiments/
```
Their roles are:
- **`task.md`** documents the task's explicit goal, scientific scope, evaluation protocol, findings, conclusions, and decisions across its experiments.
- **`program.md`** defines the workflow or research algorithm followed by the agent, as well as permissions, approval boundaries, resource limits, and stopping conditions.
- **`launch.yml`** defines deterministic executable entrypoints.
- **`experiments/`** contains mutable experiment records.


The task-level `task.md` is the canonical review document. It synthesizes task documentation, experiment branches, runs, analysis, open questions, reviewed findings, conclusions, decisions, and a final changelog. Its complete Markdown content is also used verbatim as the pull-request or merge-request description in Stage 2. The local `experiments/` directory contains mutable experiment records during their lifecycle.


**Example:**
```markdown
---
id: logreg-baseline
summary: Logistic regression baseline for heart-disease prediction
goal: Implement and tune a logistic regression baseline for the Cleveland Heart Disease dataset. The deciding metric is validation cross-entropy on the validation split.
metrics:
  - name: val/log_loss
    direction: minimize
  - name: val/accuracy
    direction: maximize
branch: hiraku/task/logreg-baseline
pr_id:
---

# Logistic regression baseline

## Goal

Reduce validation cross-entropy while training only on the training split.

## Implementation plan

## Evaluation protocol

## Findings

## Conclusions

## Decisions and next steps

## Changelog
```

### Experiment records

The PoC uses a file based experiment database. Experiment records form a local, file-backed database under `tasks/<task-id>/experiments/`. A record may use compact YAML (`<experiment-id>.yml`) or Markdown with YAML front matter (`<experiment-id>.md`). YAML is the default. A task cannot contain both extensions for the same experiment ID. Hiraku does not require the records to be committed; this is a user choice. 

Both formats share a small core schema understood by Hiraku:

```yaml
id: add-batch-norm
task_id: logreg-baseline
summary: Test batch normalization
hypothesis:
status: proposed
decision: pending
commit:
branch:
entrypoints: []
conclusion:
created_at: "2026-08-26T10:15:30.123456Z"
updated_at: "2026-08-26T10:15:30.123456Z"
```

Creation requires only `id`. Hiraku copies `task_id` from the containing task, records UTC ISO-8601 `created_at` and `updated_at` timestamps, and initializes the other fields to their empty values when they are not provided. The optional `branch` records the Git branch assigned to a Stage 2 branched experiment; an empty value denotes the linear workflow. The user may provide or update `summary`, `hypothesis`, and the other mutable fields at any time; `hiraku experiment set` refreshes `updated_at` while preserving `created_at`. The stored `task_id` must agree with the containing directory. Before a reproducible launch, the experiment must be bound to one exact `commit` and name at least one entrypoint. Stage 1 validates individual field values but does not enforce cross-field invariants or lifecycle transitions among `status`, `decision`, and `conclusion`.

Larger experiments may use Markdown for narrative documentation, the frontmatter follows the same yaml structure as outlined above

```markdown
---
id: compare-model-families
task_id: logreg-baseline
summary: Compare model families
hypothesis:
status: proposed
decision: pending
commit:
branch:
entrypoints: []
conclusion:
created_at: "2026-08-26T10:15:30.123456Z"
updated_at: "2026-08-26T10:15:30.123456Z"
---

## Objective

Identify the strongest model family under the fixed evaluation protocol.

## Implementation plan

Implement a shared preprocessing pipeline and fixed model configurations.
```

An experiment is one coherent research question or strategy evaluated from one exact source snapshot. That snapshot may be produced by several preparatory commits and may expose multiple entrypoints. All reproducible runs for an experiment use the same bound `commit`. Launches, run IDs, metrics, and parent experiments are derived from tracking records and the Git graph rather than copied into the experiment file.

Experiments can be updated throughout their lifecycle and discarded, and specify agent instructions accordingly, the default is that the final experiment record is committed. Reviewed summaries are propagated into `task.md` and, in stage 2, into pull-request or merge-request descriptions.

### Launch entrypoints

A set of named entrypoints is defined in `launch.yml` at the task level. Each entrypoint uses the full structured specification: a fixed command, working directory, environment, named arguments, and optional positional arguments. The launcher injects the task ID, experiment ID, source commit, launch provenance, tracking backend, and table path into the child environment. Example:

```yaml
entrypoints:
  train:
    command: ["uv", "run", "python", "train.py"]
    args:
      seed: 1
      regularization: 0.01
    positionals: []
    cwd: "."
```

An experiment records an ordered list of concrete launch invocations. Each string starts with an entrypoint name from `launch.yml`; the remaining tokens are appended to that entrypoint's resolved argv:

```yaml
entrypoints:
  - train
  - train --regularization 0.1
  - evaluate --split validation
```

Invocation strings use shell-like quoting only to split text into tokens; they are never executed through a shell. Pipes, redirects, substitutions, environment assignments, and other shell syntax have no special behavior. The first token must name an entrypoint and the string must not be empty. List order is preserved and exact duplicates are invalid.

For an experiment-associated launch, the requested entrypoint and trailing CLI arguments must exactly match one declared invocation after tokenization. For example, `hiraku launch --experiment stronger-reg logreg-baseline:train --regularization 0.1` matches `train --regularization 0.1`. Experiment arguments are appended after the command, positional arguments, and named arguments resolved from `launch.yml`. Consequently, repeated-option precedence is determined by the child program; an experiment invocation cannot structurally remove a default from `launch.yml`. A project that needs materially different defaults should define another named entrypoint.

### Example workflow
The following sequence illustrates the intended workflow. It can be run step-by-step or autonomously within the task program's boundaries. The data scientist can review the mutable experiment record at any point; reviewed conclusions are later summarized in `task.md` and, in stage 2, in a pull request or merge request.

```bash
# Setup
hiraku init  # creates Hiraku project configuration in `.hiraku.yml`

# 1. create task
hiraku task create logreg-baseline --template guided  # creates the task and prints branch-switch instructions
/hiraku "set the task goal to reduce validation cross-entropy"  # agent-assisted workflow
hiraku task show logreg-baseline  # task details, --json returns machine-readable output

# 2. create an experiment, then implement and bind it
hiraku experiment create logreg-baseline:stronger-regularization  --summary "Test stronger regularization"
/hiraku "test whether stronger regularization improves logreg-baseline"
git commit -m "implement stronger regularization"
hiraku experiment set logreg-baseline:stronger-regularization --commit HEAD

# 3. launch named entrypoints and inspect the tracked table
hiraku launch --experiment stronger-regularization logreg-baseline:train
hiraku runs logreg-baseline --sort val/cross_entropy --ascending

# 4. analyze the runs and update the local experiment record
/hiraku "conclude stronger-regularization and record the decision"

# 5. summarize reviewed findings in tracked task documentation
/hiraku "summarize the reviewed experiments in task.md"
hiraku runs logreg-baseline  # task leaderboard, grouped by experiment where useful
```

### Experiments: Linear workflow and branching

#### Simple linear workflow
The simplest, default workflow is linear: create a local experiment record, implement and commit its source snapshot, bind the record, launch runs, and update the record through evaluation and decision. Rejecting or deleting the local record does not add a Git commit; implementation changes can be reverted or reset using the task's normal Git workflow. The task-level `task.md` synthesizes the reviewed findings that should persist in Git.

#### Advanced branching workflow
Stage 2 adds Git experiment branches so several ideas can be explored in parallel or a task can backtrack to a prior branch or commit. A branched experiment is created with `hiraku exp create ... --branch [--from <ref>]`. The default branch is `hiraku/experiment/<task-id>/<experiment-id>`, and the selected branch is recorded in the experiment record. `--from` may resolve another branch or commit and defaults to task-branch HEAD. Without `--branch`, the experiment remains on the task branch.

If the branch already exists, Hiraku warns and attempts to switch to it without resetting it. If switching fails, Hiraku prints manual Git instructions. Worktrees, merges, conflict handling, and advancing the task branch after accepting an experiment remain normal Git operations directed by the user or task program. Hiraku does not mutate the task branch as a side effect of setting `decision: accepted`.

Experiment lineage is a DAG derived from Git commit history rather than copied into records. Experiment records retain the file-database limitation: by default they are uncommitted or committed directly to the task branch, even when implementation work occurs on an experiment branch.

### Configuration

`hiraku init` creates `.hiraku.yml`, the task directory, and the repository-local Hiraku skill.

A table-backed impl1 project uses configuration shaped like:

```yaml
tasks_dir: tasks
templates_dir: .hiraku/templates
default_template: guided
tracking_backend: table
review_provider: github  # optional; github or gitlab
review_remote: origin    # optional
```

All configured paths are repository-relative and must remain inside the project root. The table format is inferred from `.csv` or `.tsv`. 


## Command-line interface

> This section defines the intended CLI shape for the proof of concept.

Tasks are managed explicitly. Experiments are semantic records in the local task registry; each implemented experiment binds to one source commit for provenance and lineage.

### Initialize a project

```bash
hiraku init
```

This creates the default configuration, `tasks/`, complete templates under
`.hiraku/templates/{guided,auto}/`, and the agent skill `.agents/skills/hiraku/`.

### Create and inspect tasks

```bash
hiraku task create logreg-baseline
hiraku task create larger-context-window --template auto
hiraku task create custom-workflow --template my-template
hiraku task  # compact metadata for the current task
hiraku task show logreg-baseline
hiraku task show logreg-baseline --verbose
hiraku task show logreg-baseline --json
hiraku task entrypoints
hiraku task entrypoints logreg-baseline --json
```

New tasks default to the `guided` template and canonical branch `hiraku/task/<task-id>`. The branch is derived from the task ID, recorded in `task.md`, and cannot be set arbitrarily. Task front matter also contains a required one-line `summary`. Stage 2 uses it as the review title and records the persistent provider identity as optional `pr_id` for GitHub or `mr_id` for GitLab. Task creation does not switch branches; it creates the task files and prints instructions for switching to the task branch. Creation does not require a clean worktree. Each directory under `.hiraku/templates/` is a complete bundle of `task.md`, `program.md`, and `launch.yml`; projects may add their own templates.
Creation copies the selected bundle and does not record its name in the resulting task. The Hiraku skill checks that implementation work occurs on the task branch. It switches branches only when directed by the user, except that it may switch automatically when the working tree is clean and doing so is consistent with the user's instructions.

`hiraku task` is shorthand for showing the task inferred from the current task branch. Task display is compact by default and includes only front-matter metadata; `--verbose` also renders the Markdown body. JSON follows the same rule and includes `body` only with `--verbose`. `hiraku task entrypoints [task]` lists the named entrypoints defined by the selected task's current `launch.yml`.

### Create and inspect experiments

```bash
hiraku experiment create logreg-baseline:add-batch-norm --summary "Test batch normalization"
hiraku experiment create logreg-baseline:model-search --summary "Compare model families" --format markdown
hiraku experiment list
hiraku experiment list logreg-baseline
hiraku experiment show logreg-baseline:add-batch-norm
hiraku experiment set logreg-baseline:add-batch-norm --commit HEAD
hiraku experiment delete logreg-baseline:add-batch-norm
hiraku exp list logreg-baseline
hiraku exp create logreg-baseline:parallel-idea --branch --from HEAD~2
```

`hiraku exp` is an alias for `hiraku experiment`. YAML and Markdown records normalize to the same core fields. Direct editing is supported. `list` without a task resolves to the current task, and its human-readable table includes both the experiment summary and hypothesis.

### Launch named project entrypoints

Define fixed commands in the task directory, e.g., `tasks/logreg-baseline/launch.yml`:

```yaml
entrypoints:
  train:
    command: ["uv", "run", "python", "train.py"]
    args:
      seed: 1
      regularization: 0.01
    positionals: []
    cwd: "."
    env:
      MODEL_FAMILY: "logistic"
```

Launch the current repository state directly as a non-reproducible debugging execution:

```bash
hiraku launch logreg-baseline:train
```

For a reproducible launch, execute an exact commit in an isolated temporary Git worktree:

```bash
hiraku launch --commit HEAD logreg-baseline:train
hiraku launch --commit <commit-sha> logreg-baseline:train
hiraku launch --experiment add-batch-norm logreg-baseline:train
```

Flags after the entrypoint are appended to the fixed command. E.g.,

```bash
hiraku launch logreg-baseline:train --seed 42
```

`hiraku launch`:

1. resolves the task, optional experiment, and tracking configuration;
2. runs the current working tree as a non-reproducible source unless `--commit` is supplied;
3. for `--commit`, resolves the ref and creates a temporary detached worktree containing the exact committed snapshot;
4. resolves `launch.yml`, the fixed argv, safe project-relative working directory, and environment from the selected source tree;
5. injects the task ID, semantic experiment ID, source commit, launch provenance, backend type, and fixed destination `tasks/<task-id>/runs.csv` in the original project into the child environment;
6. executes the command directly without a shell and propagates its exit status; and
7. removes a temporary worktree without changing the active checkout.

The integration environment includes:

```text
HIRAKU_TASK_ID
HIRAKU_EXPERIMENT_ID
HIRAKU_SOURCE_COMMIT
HIRAKU_TRACKING_BACKEND
HIRAKU_TABLE_PATH
```

`HIRAKU_EXPERIMENT_ID` contains the stable experiment slug, while `HIRAKU_SOURCE_COMMIT` contains the resolved implementation SHA. A reproducible experiment launch uses its bound source commit; an explicitly supplied `--commit` must resolve to the same SHA. Working-tree launches remain available for debugging but are non-reproducible and do not establish a bound experiment. Hiraku deliberately does not require a row to appear after a successful child process; the launcher remains a thin local wrapper rather than a scheduler or tracking SDK.

The launcher is a thin, customizable, local command wrapper, not a scheduler or job-dispatch system.

### Inspect experiments, runs, and leaderboards

```bash
hiraku experiment list
hiraku experiment list logreg-baseline
hiraku runs
hiraku runs logreg-baseline
hiraku runs logreg-baseline --sort test/mse --ascending
hiraku runs logreg-baseline --sort accuracy --descending
hiraku runs logreg-baseline --metrics val/log_loss,val/accuracy
hiraku runs logreg-baseline --verbose
hiraku runs logreg-baseline --json
hiraku runs logreg-baseline --all  # MLflow only
```

Experiment and run listings are always scoped to one task, supplied explicitly or inferred from the current task branch. Cross-task listing is not part of the PoC. Experiment listings read the local YAML and Markdown registry and enrich each record with derived launches, runs, metrics, and Git parents.

The default human run table is a compact review view. It shows abbreviated run and commit IDs, experiment, timestamp, status when available, useful MLflow parameters, and selected metrics. It omits the redundant task ID, nested backend objects, MLflow system tags, and a false commit-mismatch field. `--verbose` shows complete flattened metadata without duplicating the nested `params`, `metrics`, and `tags` objects. Human-readable commit hashes use ten characters throughout the CLI; stored records and JSON retain exact hashes.

Metric columns come from the union of metric keys in the displayed runs. MLflow uses only native MLflow metrics; numeric parameters and tags are never inferred as metrics. For table tracking, non-provenance columns are result columns because the format does not distinguish parameters from metrics. `--metrics name,other` explicitly selects and orders human table columns. When task front matter declares `metrics`, those metrics are the default columns in declaration order and the first is the primary ranking metric. Its `minimize` or `maximize` direction supplies the default ranking direction. An explicit `--sort` may omit `--ascending` or `--descending` only when that metric has a declared direction; explicit direction flags override the declaration. Without declared or explicitly selected metrics, keys are alphabetical and the compact view shows at most six, with a warning directing the user to `--metrics` or `--verbose`.

Rows with missing or non-numeric sort values remain visible after ranked rows. JSON remains complete and lossless regardless of compact human column selection so the agent skill can consume it without scraping terminal formatting.

CSV/TSV source columns and string values are preserved. Sorted JSON rows receive an ordinal
`_rank`; rows with missing or non-numeric metrics have no rank.

The table backend requires each run producer to record a UTC ISO-8601 `created_at` timestamp. Hiraku injects launch context but does not invent or append the run row, so timestamp creation remains the responsibility of the tracking backend or training script.

### Create or update a review

Stage 2 provides:

```bash
hiraku review create [task] [--base <branch>] [--remote <name>] [--review <id>] [--push]
```

The provider is configured explicitly as `review_provider: github` or `review_provider: gitlab`; it is not inferred from the remote hostname. The optional project `review_remote` may be overridden by `--remote`. The base defaults to the remote repository's default branch.

Reviews always use the canonical task branch as head. When invoked from an experiment branch, Hiraku infers the task and reviews its task branch without changing the active checkout. The required task `summary` is the review title. `task.md` is the complete review description: the provider description is fully replaced with the file's current Markdown on every update. Each task template ends in `## Changelog`, and reviewed task updates append a concise entry there.

Hiraku resolves an existing review from `--review <id>` or the task's provider-matching `pr_id` or `mr_id`; conflicting values fail without mutation. A GitHub project rejects `mr_id`, and a GitLab project rejects `pr_id`. If no identifier is present, Hiraku searches the resolved head/base pair. Any matches, including closed reviews, are listed with instructions to select one explicitly, and Hiraku makes no provider change. If there are no matches, first-time creation requires the canonical task branch to be checked out; from an experiment branch, Hiraku stops before provider mutation and prints switch-and-rerun instructions. It then creates the review, records the returned `pr_id` or `mr_id` in `task.md`, and synchronizes the full updated file to the new review. Preview and updates using an explicit or recorded identifier remain available from experiment branches.

Hiraku never pushes implicitly. If an unpublished task branch is reviewed without `--push`, it prints `task.md` and exact instructions for publishing and rerunning. `--push` authorizes pushing only the task branch. Hiraku may create or update a pull request or merge request, but never approves or merges it.

### MLflow tracking backend

Stage 2 supports `tracking_backend: mlflow` as an optional installation extra and uses a local SQLite MLflow store only. The database defaults to `<project-root>/mlflow.db`; an optional safe project-relative `mlflow_tracking_db` overrides it. Each task specifies `mlflow_experiment` in `task.md` front matter. Remote tracking servers, registry URIs, and authentication are outside the PoC.

```yaml
tracking_backend: mlflow
mlflow_tracking_db: mlflow.db  # optional
```

Hiraku constructs an absolute `sqlite:///` tracking URI for the database under the original project root. Committed launches therefore write to the same database even though their source executes from a detached temporary worktree.

Hiraku uses MLflow's native run ID and start time as `run_id` and `created_at`. User code records `task_id`, semantic `experiment_id`, and `commit` as MLflow parameters. JSON keeps `params`, `metrics`, and `tags` namespaced. For a metric logged several times, run inspection uses MLflow's returned latest-timestamp value; metric history is not part of Stage 2. By default, listings show only runs with all required provenance and a matching task ID, while warning about omitted runs. The MLflow-only `--all` option reveals invalid, wrong-task, or unassociated runs with explicit warnings and leaves runs without numeric sort values unranked.

MLflow launches receive `HIRAKU_MLFLOW_TRACKING_URI`, `HIRAKU_MLFLOW_EXPERIMENT`, `MLFLOW_TRACKING_URI`, and `MLFLOW_EXPERIMENT_NAME` instead of `HIRAKU_TABLE_PATH`. Both backends continue to receive the common task, experiment, commit, and backend variables.

## Agent skill

`hiraku init` installs one repository-local Agent Skill at `.agents/skills/hiraku/`.

The skill acts as a router for focused workflows that are loaded when required:

- creating and refining a task;
- executing the task's program within its approved boundaries;
- creating, refining, binding, and reviewing local experiment records;
- launching and analyzing runs; and
- performing a bounded autonomous research loop.


## Agent programs 

Hiraku defines the program and boundaries that define an agent's research workflow in a task-local `program.md`. We provide default programs for `guided` and `auto` modes. Projects may define additional templates without changing the underlying task model.

> `program.md` guides agent behavior; it is not a security boundary. Enforcement remains the responsibility of Git protections, sandboxing, credentials, and execution infrastructure.

### Guided template

`guided` is the default template. It initializes a stepwise program under which the data scientist reviews the relevant artifacts before the agent proceeds through these checkpoints:

1. task scope, goal, and implementation plan;
2. experiment intent, implementation, and proposed source commit;
3. launching runs for the proposed experiment; and
4. updating findings, and conclusion.

> Under the guided program, the agent is instructed not to commit changes or launch runs without explicit human approval.

### Auto template

The `auto` template initializes a closed-loop program with explicit boundaries. The data scientist
approves the task goal and scope, evaluation protocol, permitted commands, resource bounds, and stopping
conditions before the loop starts. Within those boundaries, the agent may:

- update the implementation plan;
- create experiment records and modify and commit their implementations;
- launch runs;
- inspect and compare results;
- update findings and next steps; and
- accept/reject previous changes and select the next in-scope iteration.

The agent pauses for approval before changing the task goal or scope, evaluation, permitted commands, resource bounds, permissions, or stopping conditions in the program. Merging into a protected branch always requires explicit human approval.

Both templates produce the same data model. Their copied programs may evolve independently afterward.

## Installation

For local development, the intended workflow is:

```bash
git clone https://github.com/SwissDataScienceCenter/hiraku.git
cd hiraku
uv sync
uv run hiraku --help
```

To install `hiraku` from a checkout onto `PATH` without activating its virtual environment:

```bash
uv tool install .
hiraku --help
```

### Running the examples

Each example is a repository template. Copy it outside the Hiraku checkout before initializing it;
do not create nested Git repositories inside `examples/`:

```bash
cp -R examples/heart-disease ../hiraku-heart-disease
cd ../hiraku-heart-disease
git init -b main
hiraku init
git add .
git commit -m "initialize Heart Disease example"
```

Entrypoints and paths in an example are relative to the copied repository root. Each example
declares its own dependencies and its README continues the task-specific workflow. Automated
tests follow the same copy-and-`git init` process in temporary repositories.

The smoke and heart-disease examples select their run writer from
`HIRAKU_TRACKING_BACKEND`. Their launch wrapper uses the table dependencies by default and adds
the optional MLflow dependency automatically for an MLflow launch, while the writer uses only
the backend-specific tracking location injected by Hiraku.

Stage 2 also includes [`examples/autoresearch`](examples/autoresearch), pinned to upstream commit `228791fb499afffb54b46200aca536f79142f117`. It vendors the upstream GPU training and preparation code with MIT attribution, adapts the research program to Hiraku, and provides a CPU-only contract entrypoint for automated workflow validation.



## Proof of concept

The proof of concept is delivered in two stages. The first stage establishes a minimal working version of Hiraku. It is complete when the demo cases can execute the core loop using both guided and autonomous programs, exposing enough evidence for a human to review what happened. Metric improvement is measured, but it is not a condition for success. The second stage extends that foundation with pull-request and merge-request integration and branching experiment workflows.

### Stage 1: Minimal Working Version

Stage 1 provides the minimal vertical slice: project initialization, task creation, a local experiment registry, named entrypoint launching, CSV/TSV run tracking, ranked run inspection, and listing of experiments and runs for each task.

- **Installation and project setup**
  - Package the CLI for installation with `uv`, including its declared dependencies.
  - Initialize `.hiraku.yml`, the task directory, bundled `guided` and `auto` templates, and the repository-local agent skill.
  - Create tasks with `task.md`, `program.md`, `launch.yml`, and `experiments/`.

- **Task model and CLI**
  - Provide `init`, `task`, `experiment`, `launch`, and `runs` commands.
  - Create and inspect tasks, storing `id`, `branch`, required one-line `summary`, and one-line `goal` in `task.md` front matter, with an optional detailed goal in the body. Stage 2 uses `summary` as the review title and optional provider-matching `pr_id` or `mr_id` as the persistent review identity.
  - Derive every task branch as `hiraku/task/<task-id>`, record it without allowing arbitrary branch names, do not switch automatically, and print branch-switch instructions.
  - Resolve the current task from an explicit `[task:]object`, `--task <id>`, or the current task branch; reject conflicting selectors. Experiment-branch resolution is added with experiment branches in Stage 2.
  - Support `task create`, `task show`, and `task entrypoints`; omit `task list`.
  - Treat `hiraku task` as compact `task show` for the current task, and include the Markdown body only with `--verbose`.

- **Experiment registry**
  - Support one experiment record per ID as YAML or Markdown with YAML front matter; reject duplicate extensions and unknown structured fields.
  - Use the core fields `id`, `task_id`, `summary`, `hypothesis`, `status`, `decision`, `commit`, `branch`, `entrypoints`, `conclusion`, `created_at`, and `updated_at`, with optional `description`.
  - Create records from an ID, initializing the remaining fields, including an empty summary.
  - Record UTC creation and update timestamps; preserve `created_at` and refresh `updated_at` on CLI mutations.
  - Validate statuses as `proposed`, `active`, `evaluated`, or `abandoned`, and decisions as `pending`, `accepted`, `rejected`, or `inconclusive`; defer cross-field and lifecycle-transition validation.
  - Keep records Git-agnostic: do not automatically ignore or commit them; bundled programs recommend committing finalized records.
  - Support `experiment create`, `list`, `show`, `set`, and `delete` consistently for both record formats. `set` updates `commit`, `summary`, `hypothesis`, and other mutable fields through explicit options.
  - Provide `hiraku exp` as an alias for `hiraku experiment`, and include `hypothesis` in human-readable experiment listings.
  - Treat the bound commit as the intended executable snapshot without making it immutable, and expose run/record commit mismatches.

- **Agent skill and programs**
  - Install repository-local instructions for guided and bounded-autonomous workflows.
  - Support task goal and scope setting, experiment implementation, run launching, analysis, and conclusion recording.

- **Launcher and entrypoints**
  - Define task-level named entrypoints with the full structured `launch.yml` specification. Store experiment entrypoints as ordered concrete invocation strings whose trailing tokens are appended to the resolved task-level command.
  - Launch named entrypoints from the working tree for debugging or from an exact commit in a temporary detached worktree for reproducible execution.
  - Permit unassociated or experiment-associated working-tree launches, represented by an empty commit.
  - Execute commands directly without a shell, resolve safe project-relative paths, propagate the child exit status, and remove temporary worktrees without changing the active checkout.
  - Inject `HIRAKU_TASK_ID`, `HIRAKU_EXPERIMENT_ID`, `HIRAKU_SOURCE_COMMIT`, `HIRAKU_TRACKING_BACKEND`, and `HIRAKU_TABLE_PATH` into the child environment.
  - Do not create a separate launch registry; launch and run identifiers are provided by the tracking backend.

- **Table tracking backend**
  - Store the table implicitly at `tasks/<task-id>/runs.csv`; Stage 1 does not expose a task-level table-path option.
  - Require `run_id`, `task_id`, `experiment_id`, `commit`, and backend-recorded UTC `created_at` as provenance columns while preserving arbitrary metric columns.
  - Interpret an empty experiment ID as an unassociated run and an empty commit as a working-tree, non-reproducible run.

- **Inspection and reporting**
  - List experiments and runs for the current task, including filtering, numeric sorting, ranking, human-readable tables, and JSON output.

- **Examples and verification**
  - Include standalone repository templates for [`examples/smoke`](examples/smoke), which verifies the lifecycle with deterministic synthetic scores, and [`examples/heart-disease`](examples/heart-disease), which compares distinct logistic-regression and random-forest entrypoints on the checksum-pinned UCI Cleveland Heart Disease cohort.
  - Include unit, integration, end-to-end, lint, build, and Python 3.14 CI coverage.

### Stage 2: Branching and Reviewing

- **Experiment branching**
  - Support parallel exploration and backtracking through experiment branches.
  - Extend `hiraku experiment create` / `hiraku exp create` with the Boolean `--branch` flag and optional `--from <ref>`.
  - Derive `hiraku/experiment/<task>/<experiment>`, record it in the experiment, and switch to it. `--from` accepts another branch or commit and defaults to task-branch HEAD.
  - Warn and switch without resetting when the branch already exists; print manual instructions if switching fails.
  - Leave merging, rejection, conflict handling, and worktree orchestration to normal Git and task programs.
  - Keep task-branch advancement on acceptance as a standard Git operation outside Hiraku CLI management.

- **Review integration**
  - Provide `hiraku review create [task] [--base <branch>] [--remote <name>] [--review <id>] [--push]`; infer the task from task or experiment branches and always review the task branch.
  - Configure GitHub or GitLab explicitly in `.hiraku.yml` and use authenticated `gh` or `glab`; allow an optional configured/default remote and CLI override.
  - Default the base to the remote repository's default branch.
  - Never push without explicit `--push`; otherwise preview `task.md` and print manual publication instructions.
  - Create or update pull requests and merge requests, but never approve or merge them.
  - Select reviews by CLI or provider-matching `pr_id`/`mr_id`. Without one, list matching head/base reviews without mutation; create a review and record the appropriate identifier only when no match exists and the task branch is checked out.
  - Use `task.md` verbatim as the complete review description and fully replace the remote description on update.
  - Require `task.md` to synthesize experiment branches, runs, analysis, open questions, conclusions, and decisions, ending with an appended `## Changelog` section.

- **MLflow tracking backend**
  - Support `.hiraku.yml` configuration with `tracking_backend: mlflow`.
  - Store MLflow data in project-root `mlflow.db` by default, with a safe project-relative `mlflow_tracking_db` override.
  - Support task-level configuration for `mlflow_experiment: <experiment-name>`.
  - Support only a local SQLite MLflow store in Stage 2 and package MLflow as an optional extra.
  - Use MLflow's native run ID and start time; require user code to log `task_id`, semantic `experiment_id`, and `commit` as parameters.
  - Keep parameters, metrics, and tags namespaced, and use MLflow's latest-timestamp value for each metric key.
  - Show only provenance-complete matching-task runs by default; provide MLflow-only `--all` to reveal invalid or unassociated runs with warnings.

- **Run presentation and metric declarations**
  - Allow ordered task-level `metrics` with `minimize` or `maximize` direction.
  - Use declarations for default columns, primary ranking, and direction; otherwise derive metric columns deterministically from returned runs.
  - Provide compact default tables, `--metrics` selection, and `--verbose` flattened metadata while keeping JSON complete.
  - Abbreviate commit hashes consistently in human output while preserving exact hashes in records and JSON.

- **Autoresearch example**
  - Include [`examples/autoresearch`](examples/autoresearch), adapted from [Karpathy autoresearch](https://github.com/karpathy/autoresearch) commit `228791fb499afffb54b46200aca536f79142f117`, with its exact provenance and MIT attribution.
  - Use table tracking by default, support and test a local MLflow mode, and validate orchestration in CI through a separate behaviorally equivalent CPU mock. GPU testing remains manual.

### Stage 3: Skill refinement, documentation and additional features
This stage focusing on refining the hiraku skill, optimizing it for different research tasks, and adding user documentation.

- **SKILL REFINEMENT**
  - establish the hiraku skill as an entrypoint that with a general Hiraku overview and routes to the appropriate sub-skill based on the current context and user input.
  - define sub-skills for two main workflows:
    - review: udpate task.md, push to remote, create or update PR/MR, etc
    - research: check that the task is fully specified, this skill handles one experiment lifecycle from creation to conclusion.
  - these skills will provide a starting point for further manual refinement.
  - refine the default program.md for auto and guided templates, ensuring they are clear, concise, and effective for guiding the agent's behavior. for auto research, take inspiration from the Karpathy autoresearch program.md. Create three variants: linear with reset, linear with revert, and branching.

- **USER DOCUMENTATION**
  - refine the examples used in the readme to be more consistent and illustrative of the intended workflow. E.g. the task level example instructions should not impose the model family, experiments should provide example models like logistic regression and random forest.
  - the heart disease example should additionally have an example auto research flow.
  - add user documentation in docs/
  - add quickstart.md, tutorial.md and advanced.md

- **MLFLOW Tracking backend**
  - add support for remote tracking uri

### Stage 4: Dashboard

- **Dashboard**
  - add an experiment dashboard to visualize and review experiments.
  - start with `hiraku dashboard [task]`
  - it shows: 
    - task.md
    - list of experiments and their status, decision, and summary
    - list of runs and their metrics, sorted by a selected metric
    - in branching mode, show the lineage of experiments and their relationships
    - a plot of a selected metric over time for all experiments, with the ability to filter by experiment or status. the plot should show the performance "frontier" as in the autoresearch example.
    - the plot is interactive, hovering over a run, shows run details, experiment details. 

### Open Questions
- is cross task listing of experiments and runs needed and feasible? E.g. the records may live in different branches, have inconsistent metrics, etc.
- `hiraku run list` is missing, inconsistent with `hiraku experiment list` / make the CLI more consistent?

### Deferred

> Features listed here are considered out-of-scope for the proof of concept, but should be taken into account for later iteration.

- **Research planning and autonomy**
  - An outer loop for brainstorming or autonomously creating several task scopes.
  - Inner-loop exploration strategies.
  - Enforced program boundaries and integration with sandboxing.
  - Standardized capture of agent conversations, traces, chain-of-thought, or tool-call histories.

- **Lineage and project interface**
  - A dashboard or other graphical interface.
  - Visualization of lineage inferred from the Git DAG.
  - Synchronization, backup, or remote hosting of the local experiment registry.

- **Execution and tracking integrations**
  - HPO and scheduler integration around named entrypoints.
  - Additional run-tracking adapters, such as W&B.

## Out of scope

> Features listed here are not considered part of the Hiraku framework.

- Versioning of datasets, benchmarks, environments, or evaluation protocols is up to the user; these can be specified in code or referenced in agent instructions.


## Appendix: A naming framework for Renku AI

> The proposed naming framework came up in Chatbot interactions. It simply notes a seemingly fitting analogy between Renku-related terminology and an agentic research framework; it has been neither verified with a Japanese speaker, nor is it intended or approved for broader use. 

**Renku** — the Japanese tradition of collaborative linked verse — is proposed as a naming framework for a family of research tools. Each name draws first from its role in a Renku sequence, and from there maps onto a stage or function of research:

1. Hokku (発句) — the opening verse. It establishes the setting, atmosphere, and initial frame of the sequence. More broadly, it represents initiation and context-setting: the contribution that creates the conditions for everything that follows.
2. Wakiku (脇句) — the second verse, which closely responds to and supports the hokku. It develops the opening rather than immediately departing from it. More broadly, it represents reception, grounding, and exploration: taking an initial frame and examining what is present within it.
3. Daisan (第三) — literally “the third.” It is the first verse to create a significant shift away from the opening pair, linking to what came immediately before while introducing greater distance from the original framing. More broadly, it represents critical distance, differentiation, and redirection.
4. Hiraku (平句) — the ordinary or regular verses that make up most of the body of a renku. They are where the sequence develops, moves across subjects, changes perspective, and opens new directions. More broadly, hiraku represents the generative middle: investigation, iteration, experimentation, and sustained development.
5. Ageku (挙句) — the concluding verse. It brings the sequence to a satisfying close without necessarily summarizing or exhausting everything that came before. More broadly, it represents completion and release: bringing a sequence to an end while leaving space for what may follow.

Together, the proposed naming convention frames research as a sequence of **linked contributions that establish, explore, challenge, develop, and communicate ideas**.


|Component|Role|Renku principle|
|---|---|---|
|**Hokku**|Knowledge and literature|Establish|
|**Wakiku**|Data exploration|Explore|
|**Daisan**|Review and verification|Challenge|
|**Hiraku**|AI-assisted research tooling|Investigate|
|**Ageku**|Papers, presentations and posters|Communicate|
