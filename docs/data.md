# Hiraku Data Model

### Task record: `task.md`

Each task is defined by a unique id and corresponding markdown file.
```text
hiraku/tasks/{task_id}/task.md
```
It synthesizes task documentation, experiment branches, runs, analysis, open questions, reviewed findings, conclusions, decisions, and a final changelog. The markdown is continuously updated throughout the task lifecycle and provides a primary reviewing surface for the data scientist (for example, task.md my be used as description for a pull/merge request). 

At any time, there is a single **active task**. `hiraku task set <task-id>` records it in the local Hiraku context. Commands that accept `--task` use it as an explicit override; otherwise they resolve the task from `HIRAKU_TASK_ID`, the local context, or the current task branch. Creating a task makes it active unless `--no-set` is supplied. 


The Markdown front matter records key-value pairs as machine-readable YAML. All
records use Markdown files; the Markdown body may be empty.

Hiraku differentiates core fields and user fields. Core fields carry semantic meaning and are directly used by the Hiraku framework to manage experiments and tasks. User fields  extend functionality, define defaults, configure agent instructions, or add user managed output when listing tasks or experiments.

- Core fields are: `id`, `branch` (default: `hiraku/task/<task-id>`)
- Optional core fields are: `metrics`, which defines defaults for listing and sorting runs, `entrypoints`, and `program`
- Default user fields include: `goal`, `question`, `summary`

**Example:**

```markdown
---
id: heart-disease  
branch: hiraku/task/heart-disease  
summary: Improve heart-disease risk prediction  
goal: Minimize validation log loss on the checksum-pinned Cleveland cohort.   
question: What is the best way to approach this problem?  
program: interactive
metrics: 
  - name: val/log_loss
    direction: minimize
  - name: val/accuracy
    direction: maximize
entrypoints:
  evaluate:
    command: ["uv", "run", "python", "evaluate.py"]
---
# Heart-disease risk prediction

## Goal

Minimize validation log loss while training only on the fixed training split.

## Implementation plan

## Evaluation protocol

## Results

## Changelog
```


**CLI:**
```
hiraku task create <task-id> --branch main
hiraku task create <task-id>  # default branch is hiraku/tasks/<task-id>
hiraku task create <task-id> --goal "goal description" --question "research question"
hiraku task set <task-id>
hiraku task update <task-id> --summary "summary text"
```

Task entrypoints support operations that apply to the task as a whole, such as preparing data or running a shared evaluation. Experiment entrypoints support operations specific to one investigation, such as training a particular model. These scopes are complementary: a user may define and launch task entrypoints, experiment entrypoints, or both.

### Experiment records

An experiment is a record that points to a code commit and may define execution
entrypoints. The code commit can be initially empty; experiments without a code
commit are called proposals. A bound `commit` is stored as a full lowercase Git
hash: 40 hexadecimal characters for SHA-1 or 64 for SHA-256. It is stored within
the task directory:
```bash
hiraku/tasks/{task_id}/experiments/{experiment_id}.md
```
It is a mutable record that can be updated throughout the experiment lifecycle. 

Experiment records form a local, file-backed database under
`hiraku/tasks/{task_id}/experiments/`. Every record is a Markdown file with YAML
front matter and an optional Markdown body.


Hiraku does not require the records to be committed; this is a user choice. When listing experiments, Hiraku will use the following conventions:
- it lists experiments that are currently visible in the workspace.
- if the current branch is not the task branch, it will additional list experiments from the branch defined by the current task.

As for tasks, experiments define core fields and user-defined fields.
- Core fields: `id`, `task_id`, `commit` (can be None), `entrypoints`, `created_at`, `updated_at`
- Optional core fields: `branch` (experiment branch)
- default user fields are: `summary`, `hypothesis`, `type`, `status`, `decision`, `conclusion`


```markdown
---
id: logistic-baseline
task_id: heart-disease
commit:
branch:
summary: Logistic regression baseline
hypothesis: Regularized logistic regression provides a calibrated baseline.
status: proposed
decision: pending
entrypoints: 
  train:
    command: ["uv", "run", "python", "train.py"]
    args:
      model: logistic
      seed: 42
conclusion:
created_at: "2026-08-26T10:15:30.123456Z"
updated_at: "2026-08-26T10:15:30.123456Z"
---
```

Experiment creation requires only `id`. `task_id` defaults to the active task. At creation Hiraku records UTC ISO-8601 `created_at` and `updated_at` timestamps and initializes unspecified fields to empty values. The new experiment and its task become active unless `--no-set` is supplied. The optional `branch` defines an experiment branch used for implementing the experiment. This enables parallel exploration of hypotheses or isolated experiment worktrees. `entrypoints` defines experiment-scoped execution entrypoints.

An optional **active experiment** is selected with `hiraku experiment set
<experiment-id>`.

Active selections are stored in the project configuration, `hiraku.yml`:

```yaml
task_id: heart-disease
experiment_id: logistic-baseline
program_id: interactive
```

Environment variables provide non-persistent overrides for CI, agents, and
isolated commands; `set` commands update `hiraku.yml` rather than attempting to
modify the parent shell environment.

Experiment Markdown bodies provide narrative documentation; their YAML front
matter follows the same structure outlined above.

```markdown
---
id: random-forest
task_id: heart-disease
summary: Random forest comparison
hypothesis: Nonlinear feature interactions improve validation log loss.
status: proposed
decision: pending
commit:
branch:
entrypoints: []
conclusion:
created_at: "2026-08-26T10:15:30.123456Z"
updated_at: "2026-08-26T10:15:30.123456Z"
---

## Proposal 

## Objective

Identify the strongest model family under the fixed evaluation protocol.

## Implementation plan

Implement a shared preprocessing pipeline and fixed model configurations.
```

An experiment is one coherent research question or strategy with an optional
bound source snapshot. The local dispatcher runs the current state without
commit attribution; the worktree dispatcher checks out and attributes the
selected commit exactly. Launches, run IDs, metrics, and parent experiments are
derived from tracking records and the Git graph rather than copied into the
experiment file.

Experiments can be updated throughout their lifecycle and discarded, and specify agent instructions accordingly, the default is that the final experiment record is committed. Reviewed summaries are propagated into `task.md` and, in stage 2, into pull-request or merge-request descriptions.

**CLI**

```bash
hiraku experiment create <exp-id>
hiraku experiment create logistic-regression
hiraku experiment create <exp-id> --commit HEAD
hiraku experiment set <exp-id>
hiraku experiment update <exp-id> --entrypoint train

hiraku launch <entrypoint>                         # current task entrypoint
hiraku launch <exp-id>:<entrypoint>                # experiment in the current task
hiraku launch logistic-regression:train -- --seed 1  # arguments after -- override entrypoint defaults
hiraku launch <entrypoint> --task <task-id>        # task entrypoint in an explicit task
```

#### Reproducible launches

Dispatchers control Git behavior. The default `launch.sh` runs the current
working state without commit attribution. Use `worktree` to run and attribute
an exact commit in an isolated worktree:
```bash
hiraku launch --dispatch worktree logistic-regression:train
hiraku launch --dispatch worktree --commit ab15ad41 logistic-regression:train
```

#### Launch entrypoints

A task or experiment can define named entrypoints. `<entrypoint>` resolves only
at task level and carries no experiment attribution.
`<experiment-id>:<entrypoint>` first checks the experiment and then inherits the
task definition; an experiment definition replaces the complete task definition.
The selector always supplies experiment attribution.

#### Dispatch backends
The user can define a custom dispatch backend in
```bash
.hiraku/dispatch/<dispatch-backend>.sh
```

Hiraku invokes a selected backend with context options followed by the full
launch command after `--`, e.g.
```bash
hiraku launch --dispatch runai logistic-regression:train
```
Translates to
```bash
hiraku/dispatch/runai.sh --task-id my-task --experiment-id logistic-regression --entrypoint train -- uv run train.py ...
```

The project owns its dispatch scripts. New projects receive local `launch.sh`
and isolated `worktree.sh` defaults that can be customized. Their child output
streams directly, and `--dispatch worktree` resolves `worktree.sh` without
requiring the extension.

### Runs

User managed, backend. Hiraku only queries results. the backend returns records in the following format:

```json
{
  run_id: <run-id>,  # backend defined unique identifier
  task_id: <task-id>,
  experiment_id: <exp-id>,
  commit: <hash>,
  timestamp: <timestamp>,  
  params  # entrypoint, params
  metrics:
    train/mse: 1.2
    val/mse: 1.4
}
```
