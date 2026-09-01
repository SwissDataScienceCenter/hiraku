# Hiraku

**Hiraku** is a toolkit for AI-assisted data science.

The Hiraku core implementation defines conventions and customizable workflows around the model development lifecycle of data science and machine learning projects. A shared metadata record enables both interactive human–AI collaboration and autonomous research, with explicit boundaries and checkpoints for reviewing and verification. 

The toolkit provides deterministic support functions via a command-line interface (`hiraku`) and agent integration with the `/hiraku` skill, harness (tbd), and sandboxing (tbd).

**Why Hiraku?**
In renku (連句), the traditional Japanese art of collaborative linked poetry, hiraku (平句) are the regular verses where the sequence develops, shifts, and explores new directions — a  metaphor for iterative, exploratory research within the [Renku ecosystem](docs/RENKU.md).


## The Model-Development Lifecycle

In the experimental phase of a data science project, the problem, data, and objective are typically well defined, and the focus shifts to iterative model development (cf. [CRISP-ML](https://ml-ops.org/content/crisp-ml)). For a given task with a defined goal, data scientists typically cycle through a sequence of steps:

1. defining an experiment (e.g., a baseline or hypothesis),
2. implementation and debugging,
3. executing runs and tracking metrics,
4. analyzing and comparing results, and
5. iterating or concluding.

### The Problem: Metadata tracking

While standard tools support individual stages of this cycle (Git for version control, Weights & Biases or MLflow for experiment tracking, and notebooks for analysis, etc), the meta-level information that connects these stages is often only loosely documented.

This fragmentation creates an inherent barrier for effective human-AI collaboration: proposals, hypotheses, implementation, experiments, diagnostics, results, and conclusions are not consistently linked or documented as part of a coherent research process. Without a lightweight but explicit protocol for capturing the research process, AI systems cannot reliably inspect prior work, understand why decisions were made, propose meaningful next steps, to effectively support data scientists through iterative experimentation. Similarly, without a metadata trace, the data scientist cannot track, inspect and verify the output of an autonomous research agent.



## Hiraku core

Hiraku addresses this gap by linking metadata, workflows and agent execution. Hiraku CLI (`hiraku`) and agent skill (`/hiraku`) support task creation, experiment implementation, documentation and analysis.


### Data model: Tasks, experiments, runs

Hiraku uses four lightweight data structures: tasks, experiments, runs, and programs. Data records are saved in YAML or Markdown with YAML front matter. A *task* defines a working scope and research goal. *Experiments* are smaller units that link a task, hypothesis, code, and results. Tasks and experiments may each define execution entrypoints. A *program* defines an agent workflow and its boundaries. *Runs* record execution traces for a specified task or experiment and code commit.


| Term | Data | Summary |
| --- | ---  | --- |
| **Task** | `hiraku/tasks/<task-id>/task.md` or `task.yml` | A task is the top-level Hiraku abstraction that defines the scope of a research effort. A task defines any of the following: a research question, a goal, metrics and records scientific documentation of the research task, including scope, methodology, and results. |
| **Proposal / Experiment** | `hiraku/tasks/<task-id>/experiments/<experiment-id>/experiment.md` or `experiment.yml` | An *experiment* is a focused investigation within a task that refers to a source commit and may define execution entrypoints. An experiment without a reference commit is called a *proposal*. Its intent is user-defined: it can be a baseline, hypothesis, diagnosis, model run, ablation, or anything else; Hiraku does not impose specific semantics. A typical experiment lifecycle spans proposal, implementation, execution, analysis, and a record of the findings. The experiments associated with a task provide an overview of the research effort. |
| **Run** | (backend owned execution trace) | A single execution trace of an experiment. The tracking backend and logging is user managed by the user, Hiraku only queries results via compatibility backend. Example backends: CSV, W&B, MLFlow, custom user backend.  |


### Agent integration: Programs, Actions, Contexts

| Term | Data | Summary |
| --- | ---  | --- |
| **Program** | `hiraku/programs/<program-id>/program.md` | A named agent workflow that defines instructions, permissions, resource limits, checkpoints, and stopping conditions. |
| **Action** | `hiraku/actions/<entity>/<action-id>/action.md` | A named action that can be invoked by the hiraku-auto agent. |
| **Context** | `hiraku/contexts/<context-id>/context.md` or `hiraku/context/<context-id>.md` | Current context files, e.g. literature, cross-task summaries, etc. |


  > Hiraku is agnostic to git state of meta data as far as possible. The user my commit the metadata files to to git repository or leave it untracked. Hiraku simply lists what is currently visible in the working directory, or tries to detect md files on the defined task branch.
  

#### Ideas:

- **Context** can be another first-class entity<br/>
I.e. hiraku context create literature-search puts the context in hiraku/context/literature-search/context.md
- **Action** can be another first-class entity, defining "skills" for the hiraku-agent:<br/>
E.g., hiraku action create experiment/accept "Accept an experiment proposal"
Creates a "skill" in hiraku/actions/experiment/accept/action.md which is invoked with
hiraku-auto experiment accept <experiment-id> , we can ship a few default actions. Ideally, actions are just "programs"?



### Task record: `task.md`

Each task is defined by a unique id and corresponding markdown file.
```text
tasks/<task-id>/task.md
```
It synthesizes task documentation, experiment branches, runs, analysis, open questions, reviewed findings, conclusions, decisions, and a final changelog. The markdown is continuously updated throughout the task lifecycle and provides a primary reviewing surface for the data scientist (for example, task.md my be used as description for a pull/merge request). 

At any time, there is a single **active task**. `hiraku task set <task-id>` records it in the local Hiraku context. Commands that accept `--task` use it as an explicit override; otherwise they resolve the task from `HIRAKU_TASK_ID`, the local context, or the current task branch. Creating a task makes it active unless `--no-set` is supplied.


The markdown frontmatter records key-value pairs as machine-readable yaml. Markdown is optional, if the user does not use markdown, the task file can also be saved as yaml (`task.yml`). 

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

An experiment is a record that points to a code commit and may define execution entrypoints. The code commit can be initially empty; experiments without a code commit are called proposals. It is stored within the task directory:
```bash
tasks/<task-id>/experiments/<experiment-id>.yml
```
It is a mutable record that can be updated throughout the experiment lifecycle. 

Experiment records form a local, file-backed database under `tasks/<task-id>/experiments/`. A record may use compact YAML (`<experiment-id>.yml`) or Markdown with YAML front matter (`<experiment-id>.md`). YAML is the default. A task cannot contain both extensions for the same experiment ID. 


Hiraku does not require the records to be committed; this is a user choice. When listing experiments, Hiraku will use the following conventions:
- it lists experiments that are currently visible in the workspace.
- if the current branch is not the task branch, it will additional list experiments from the branch defined by the current task.

As for tasks, experiments define core fields and user-defined fields.
- Core fields: `id`, `task_id`, `commit` (can be None), `entrypoints`, `created_at`, `updated_at`
- Optional core fields: `branch` (experiment branch)
- default user fields are: `summary`, `hypothesis`, `type`, `status`, `decision`, `conclusion`


```yaml
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
```

Experiment creation requires only `id`. `task_id` defaults to the active task. At creation Hiraku records UTC ISO-8601 `created_at` and `updated_at` timestamps and initializes unspecified fields to empty values. The new experiment and its task become active unless `--no-set` is supplied. The optional `branch` defines an experiment branch used for implementing the experiment. This enables parallel exploration of hypotheses or isolated experiment worktrees. `entrypoints` defines experiment-scoped execution entrypoints.

An optional **active experiment** is selected with `hiraku experiment set <experiment-id>`. Setting an experiment also sets its owning task. Commands that accept `--experiment` use it as an explicit override; otherwise they resolve the experiment from the launch target, `HIRAKU_EXPERIMENT_ID`, or the local context.

Active selections are stored in `.hiraku/context.yml`:

```yaml
task_id: heart-disease
experiment_id: logistic-baseline
program_id: interactive
```

The context file is local state and must be excluded from Git. Environment variables provide non-persistent overrides for CI, agents, and isolated commands; `set` commands update the context file rather than attempting to modify the parent shell environment.

Experiments may use Markdown for narrative documentation, the frontmatter follows the same yaml structure as outlined above

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

An experiment is one coherent research question or strategy evaluated from one exact source snapshot. That snapshot may be produced by several preparatory commits and may expose multiple entrypoints. All reproducible runs for an experiment use the same bound `commit`. Launches, run IDs, metrics, and parent experiments are derived from tracking records and the Git graph rather than copied into the experiment file.

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
hiraku launch logistic-regression:train --seed 1  # kwargs that come after the entrypoint name are used as overwrites for the script
hiraku launch <entrypoint> --experiment <exp-id>   # task entrypoint attributed to an experiment
hiraku launch <entrypoint> --task <task-id>        # task entrypoint in an explicit task
```

#### Reproducible launches

An optional `--commit` flag is used to launch the experiment in an isolated worktree at the defined commit, e.g.
```bash
hiraku launch --commit ab15ad41 logistic-regression:train
```

#### Launch entrypoints

A task or experiment can define a set of named entrypoints. Each entrypoint uses the same structured specification: a fixed command, working directory, environment, named arguments, and optional positional arguments. The two namespaces remain separate: `<entrypoint>` selects a task entrypoint, while `<experiment-id>:<entrypoint>` selects an experiment entrypoint. The same name may therefore exist at both levels without shadowing or merging. The launcher injects the task ID, optional experiment ID, source commit, and tracking backend information.

#### Dispatch backends
The user can define a custom dispatch backend in
```bash
.hiraku/dispatch/<dispatch-backend>.sh
```

Hiraku invokes a selected backend by passing the full launch command after a ` -- `, e.g
```bash
hiraku launch --dispatch runai logistic-regression:train
```
Translates to
```bash
.hiraku/dispatch/runai.sh -- uv run train.py ...
```

> Launch invocation is not recorded by default. If the user want to record launches they can modify the default launch script in
```
.hiraku/dispatch/_default.sh
```

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
### Agent integration

Hiraku programs define agent instructions for research workflows. Each `program.md` records explicit boundaries and permissions, resource limits, stopping conditions, and natural-language instructions such as hypothesis generation and user confirmation. In the simplest setting, the program is interpreted by the LLM without system-enforced boundaries. Later integration with a harness or sandbox may enforce these constraints.

Programs are first-class records managed by the Core API and CLI:

```bash
hiraku program create my-program
hiraku program list
hiraku program show interactive
hiraku program set autoresearch
hiraku program update my-program --summary "Custom bounded workflow"
hiraku program delete my-program
```

`create` makes the new program active unless `--no-set` is supplied. `set` updates `program_id` in `.hiraku/context.yml`; `update` modifies the program record. Direct editing remains supported. Deleting a program that is active or referenced by a task is rejected unless explicitly forced.

Programs take precedence in this order:
```
--program <program-id>
HIRAKU_PROGRAM_ID
.hiraku/context.yml
the task record's `program` field
the repository configuration's `program` field
interactive
```

Programs are saved in the Hiraku folder:
```
.hiraku/programs/<program-id>/program.md
```

By default, Hiraku ships with two workflows:

- **interactive:** A step-by-step program where the data scientist explicitly approves each checkpoint and reviews artifacts before proceeding.
- **autoresearch:** A bounded autonomous loop with explicit permissions, resource limits, and stopping conditions under which the agent implements, runs, analyzes, and iterates, exposing all artifacts for post-hoc human verification.


**Example**
> ToDo: Define agent integration and core fields. Right now, only id is defined, everything else is interpreted as skill. Later we can pass precise instructions to a custom Harness or sandbox.

```markdown
---
id: interactive
summary: A interactive, step-by-step workflow with explicit human approval and review.
gates:
  - git commit
  - git push
allowed:
  - hiraku task show
  - hiraku experiment create
  - hiraku experiment update
  - hiraku launch
  - hiraku runs
disallowed:
  - hiraku experiment delete
  - hiraku experiment sign
  - hiraku task conclude
branch-mode: linear  # restricts experiments to the task branch; no branching or merging is allowed.
limits:
  time: 5 min
  memory: 16 GB
  gpu: 16 GB
  episodes: 1000
---
# Interactive workflow 
<ToDo: Fill example with agent instructions.>

```


#### Agent Skill

The simplest way to use Hiraku is with a Coding harness, e.g. Codex, Claude, Pi, ..., and invoke the `/hiraku` kill with natural language instructions. The instructs the agent to follow Hiraku workflows and read the relevant files.

`hiraku init` installs one repository-local Agent Skill at `.agents/skills/hiraku/`.

The skill acts as a router for focused workflows that are loaded when required:

- creating and refining a task;
- executing the task's program within its approved boundaries;
- creating, refining, binding, and reviewing local experiment records;
- launching and analyzing runs; and
- performing a bounded autonomous research loop.


#### Hiraku Agent

> ToDo: `hiraku-auto` or `hiraku-agent` ?

The hiraku agent invokes actions that are defined in `hiraku/actions/<entity>/<action-id>.md`. E.g., `hiraku auto experiment create` invokes `hiraku/actions/experiment/create/action.md`. The action defines the agent's instructions, permissions, and boundaries. The user can define custom actions for their own workflows. Action combine deterministic hooks with natural-language instructions.

```bash
hiraku-auto experiment create heart-disease:logistic-baseline --summary "Logistic regression baseline" --hypothesis "Regularized logistic regression provides a calibrated baseline" --entrypoint logistic --agent pi --program interactive
```

```bash
hiraku-auto experiment create heart-disease:logistic-baseline --summary "Logistic regression baseline" --hypothesis "Regularized logistic regression provides a calibrated baseline" --entrypoint logistic --agent pi --program autoresearch
```



#### Hiraku Harness
   > ToDo: Create Pi extension that explicitly parses programs and enforces the workflow and boundaries.

### Configuration

`hiraku init` creates `.hiraku.yml`, `.hiraku/context.yml`, the task directory, the bundled `interactive` and `autoresearch` programs, and the repository-local Hiraku skill. It adds the local context file to `.gitignore`.

A table-backed project uses configuration shaped like:

```yaml
task_path: hiraku/tasks/<task-id>/task.[md|yml] 
experiment_path: hiraku/tasks/<task-id>/experiments/<experiment-id>.[md|yml]
context_path: hiraku/context/<context-id>.md
action_path: hiraku/actions/<entity>/<action-id>.md
program_path: hiraku/programs/<program-id>/program.[md|yml]
task_branch: hiraku/tasks/<task-id>
default_program: interactive
tracking_backend: table
```

  > Todo: it can also make sense to define the tracking backend at the task level. Another idea is to define defaults that can be overwritten at task or experimet level (e.g., branch, program, ...). Then we add `program` as an optional key to task, and experiments.

  > This can also be a place to configure default fields for listing experiments and tasks

All configured paths are repository-relative and must remain inside the project root. The table format is inferred from `.csv` or `.tsv`. 


### Installation

Choose the level of detail you need: [Quickstart](docs/quickstart.md),
[linear tutorial](docs/tutorial.md), or
[advanced branching, reviews, and remote tracking](docs/advanced.md).

For local development, the intended workflow is:

```bash
git clone https://github.com/SwissDataScienceCenter/hiraku.git
cd hiraku
uv sync
uv run hiraku --help
```

Install optional dashboard support with `uv sync --extra dashboard`, or both dashboard and MLflow
with `uv sync --extra dashboard --extra mlflow`.

To install `hiraku` from a checkout onto `PATH` without activating its virtual environment:

```bash
uv tool install .
hiraku --help
```

## Example workflow
The following sequence illustrates the intended workflow. It can be run step-by-step or autonomously within the task program's boundaries. The data scientist can review the mutable experiment record at any point; reviewed conclusions are later summarized in `task.md` and, in stage 2, in a pull request or merge request.

```bash
# Setup
hiraku init  # creates Hiraku project configuration in `.hiraku.yml`

# 1. create task
hiraku task create heart-disease  # creates and sets the active task
/hiraku "define the fixed heart-disease evaluation protocol"  # agent-assisted workflow
hiraku task show heart-disease  # task details, --json returns machine-readable output

# 2. create an experiment, then implement and bind it
hiraku experiment create logistic-baseline --summary "Logistic regression baseline" --hypothesis "Regularized logistic regression provides a calibrated baseline" --entrypoint logistic  # creates and sets the active experiment
/hiraku "implement the approved logistic baseline"
git commit -m "implement logistic baseline"
hiraku experiment update heart-disease:logistic-baseline --commit HEAD

# 3. launch named entrypoints and inspect the tracked table
hiraku launch logistic-baseline:train
hiraku runs heart-disease --sort val/log_loss --ascending

# 4. analyze the runs and update the local experiment record
/hiraku "conclude logistic-baseline and record the decision"

# 5. summarize reviewed findings in tracked task documentation
/hiraku "summarize the reviewed experiments in task.md"
hiraku runs heart-disease  # task leaderboard, grouped by experiment where useful
```

### Experiments: Linear workflow and branching

#### Simple linear workflow
The simplest, default workflow is linear: create a local experiment record, implement and commit its source snapshot, bind the record, launch runs, and update the record through evaluation and decision. Rejecting or deleting the local record does not add a Git commit; implementation changes can be reverted or reset using the task's normal Git workflow. The task-level `task.md` synthesizes the reviewed findings that should persist in Git.

#### Advanced branching workflow
Experiment branches can be used to explore several ideas in parallel or a task can backtrack to a prior branch or commit. A branched experiment is created with `hiraku experiment create ... --branch [--from <ref>]`. The default branch is `hiraku/experiment/<task-id>/<experiment-id>`, and the selected branch is recorded in the experiment record. `--from` may resolve another branch or commit and defaults to task-branch HEAD. Without `--branch`, the experiment remains on the task branch.

If the branch already exists, Hiraku warns and attempts to switch to it without resetting it. If switching fails, Hiraku prints manual Git instructions. Worktrees, merges, conflict handling, and advancing the task branch after accepting an experiment remain normal Git operations directed by the user or task program. Hiraku does not mutate the task branch as a side effect of setting `decision: accepted`.

Experiment lineage is a DAG derived from Git commit history rather than copied into records. Experiment records retain the file-database limitation: by default they are uncommitted or committed directly to the task branch, even when implementation work occurs on an experiment branch.


## Hiraku Core API

The Hiraku Core API is the authoritative definition of the data model and lifecycle actions for tasks, experiments, entrypoints, programs, and runs. The CLI and other user interfaces, such as dashboards and TUIs, are thin interfaces built on top of this API; they do not define separate lifecycle semantics.

### Records

`Record` is the shared base class for file-backed Hiraku data structures such as `Task`, `Experiment`, and `Program`. It provides YAML and Markdown serialization, validation, and dictionary-like field access. Concrete record types declare three field categories:

- **Required core fields** are required and interpreted by Hiraku.
- **Optional core fields** are validated and interpreted when present.
- **User fields** are preserved without framework-defined semantics.

Markdown records store their fields as YAML front matter and expose the remaining document as `record.markdown`. YAML records contain fields only. Loading and saving a record preserves user fields and Markdown content. A registry cannot contain both `<id>.yml` and `<id>.md` for the same record ID.

```python
class Task(Record):
    required_fields = {"id", "branch"}
    optional_fields = {
        "metrics": list,
        "entrypoints": dict,
    }

task = Task.load("tasks/heart-disease/task.md")
print(task.id, task["goal"], task.get("custom_field"))
task["custom_field"] = "value"
task.save()
```

The base class is responsible only for field access, validation, and serialization. Lifecycle actions and domain-specific behavior remain on the concrete types.


Python API

```python
from hiraku.core import Record, Task, Experiment, Run, Program, Entrypoint
from hiraku.tracking import TableBackend, MLflowBackend, WandBBackend
from hiraku.config import HirakuConfig
from hiraku.agent import Agent

task_markdown = """# Heart-disease risk prediction
### Goal
Minimize validation log loss while training only on the fixed training split.
### Implementation plan
### Evaluation protocol
### Findings
### Conclusions
### Decisions and next steps
### Changelog
"""

HirakuConfig.init(install_skills=True)

config = HirakuConfig()
config.tasks_dir


task = Task("heart-disease", goal="Minimize validation log loss on the checksum-pinned Cleveland cohort.", question="What is the best way to approach this problem?", summary="This is a summary of the task.", tracking_backend="table", branch="hiraku/task/heart-disease", markdown=markdown)

Task.load("heart-disease")  # creates tasks/heart-disease/task.md
Task.load("tasks/heart-disease/task.md")  # creates tasks/heart-disease/task.md

print(task["goal"], task.goal)
print(task["question"], task.question)
print(task["summary"], task.summary)
print(task.markdown)
task.save()  # creates tasks/heart-disease/task.md
task.check_branch()
task.switch_branch()

task = Task.load("heart-disease")

tasks = Task.list()


for task in tasks:
    print(task.id, task.goal, task.tracking_backend, task.branch)


experiment = Experiment(task, "logistic-baseline", summary="Logistic regression baseline", hypothesis="Regularized logistic regression provides a calibrated baseline.", commit="HEAD", entrypoints=dict(train="uv run python train.py --model logistic --seed 42")) ], markdown=markdown, branch=None)

experiment.save()  # creates tasks/heart-disease/experiments/logistic-baseline.yml or logistic-baseline.md if markdown is provided


experiment.branch
experiment["branch"]

experiment.check_branch()
experiment.switch_branch()



experiment.parents()
experiment.children()

task.launch("evaluate")  # task entrypoint without experiment attribution
task.launch("evaluate", experiment=experiment)  # task entrypoint for an experiment
experiment.launch("train", worktree=True, commit=experiment.commit, args={"model": "logistic", "seed": 42})
experiment.entrypoints()["train"].dispatch("runai.sh")



experiments = task.experiments()

runs = Run.list(task)
for run in runs:
    print(run.id, run.experiment_id, run.metrics, run.commit)

tracking_backend = task.get_tracking_backend()
runs = task.runs()





# write operations are note the point, scripts should directly log to standard backends.
# however for the table backend, we can have some api support for logging.
# Maybe in the future, we can have a more unified API. allowing to switch between backends, etc
tracking_backend.log_run(experiment_id="logistic-baseline", metrics={"val/log_loss": 0.123, "val/accuracy": 0.987}, commit="HEAD", entrypoint="train", args={"model": "logistic", "seed": 42}) 


program = Program.load("interactive")
programs = Program.list()
program.save()
agent = Agent(program=program, task=task, experiment=experiment, backend="Codex")
agent.run() 
```

## Command-line interface

> This section defines the intended CLI shape. The CLI directly invokes the core Python API.

Tasks are managed explicitly. Experiments are semantic records in the local task registry; each implemented experiment binds to one source commit for provenance and lineage.

### Initialize a project

```bash
hiraku init
```

This creates the default configuration, local context, `tasks/`, the bundled programs under
`.hiraku/programs/{interactive,autoresearch}/`, and the agent skill `.agents/skills/hiraku/`. The local `.hiraku/context.yml` is added to `.gitignore`.

### Create and inspect tasks

```bash
hiraku task create heart-disease
hiraku task create heart-disease-search --program autoresearch
hiraku task create custom-workflow --program custom --no-set
hiraku task set heart-disease
hiraku task update heart-disease --summary "Improved baseline study"
hiraku task  # compact metadata for the current task
hiraku task show heart-disease
hiraku task show heart-disease --verbose
hiraku task show heart-disease --json
hiraku task entrypoints
hiraku task entrypoints heart-disease --json
```

New tasks default to the `interactive` program and canonical branch `hiraku/task/<task-id>`. The selected program is recorded in `task.md`. The branch is derived from the task ID, recorded in `task.md`, and cannot be set arbitrarily. Task front matter also contains a required one-line `summary`. Stage 2 uses it as the review title and records the persistent provider identity as optional `pr_id` for GitHub or `mr_id` for GitLab. Task creation sets the new task as active unless `--no-set` is supplied. It does not switch Git branches; it creates the task files and prints instructions for switching to the task branch. Creation does not require a clean worktree.
The Hiraku skill checks that implementation work occurs on the task branch. It switches branches only when directed by the user, except that it may switch automatically when the working tree is clean and doing so is consistent with the user's instructions.

`hiraku task` is shorthand for showing the task inferred from the current task branch. Task display is compact by default and includes only front-matter metadata; `--verbose` also renders the Markdown body. JSON follows the same rule and includes `body` only with `--verbose`. `hiraku task entrypoints [task]` lists the named entrypoints defined in the selected task record.

### Create and inspect experiments

```bash
hiraku experiment create heart-disease:logistic-baseline --summary "Logistic regression baseline" --entrypoint logistic
hiraku experiment create heart-disease:random-forest --summary "Random forest comparison" --entrypoint random-forest --format markdown
hiraku experiment list
hiraku experiment list heart-disease
hiraku experiment show heart-disease:logistic-baseline
hiraku experiment set heart-disease:logistic-baseline
hiraku experiment update heart-disease:logistic-baseline --commit HEAD
hiraku experiment delete heart-disease:logistic-baseline
hiraku experiment create heart-disease:stronger-regularization --branch --from HEAD --no-set
```

`hiraku experiment create` sets the experiment and its task as active unless `--no-set` is supplied. `set` changes the active experiment context; `update` modifies an experiment record. `hiraku exp` is a shorthand for `hiraku experiment`. YAML and Markdown records normalize to the same core fields. Direct editing is supported. `list` without a task resolves to the current task, and its human-readable table includes both the experiment summary and hypothesis.

### Create and inspect programs

```bash
hiraku program create my-program
hiraku program create review-loop --no-set
hiraku program list
hiraku program show interactive
hiraku program set autoresearch
hiraku program update my-program --summary "Custom bounded workflow"
hiraku program delete my-program
```

Programs are Markdown records under `.hiraku/programs/`. `create` sets the new program as active unless `--no-set` is supplied. `set` changes the active program context; `update` modifies the program record. The bundled `interactive` and `autoresearch` programs follow the same data model as user-defined programs.

### Launch named entrypoints

Tasks and experiments may each define named entrypoints in their records. For example, a task may define a shared evaluation command:

```yaml
entrypoints:
  evaluate:
    command: ["uv", "run", "python", "evaluate.py"]
    args:
      seed: 42
    positionals: []
    cwd: "."
```

An experiment may independently define a model-specific training command using the same structure. Task and experiment entrypoints are complementary and are not merged. The launch target selects which scope to use:

```bash
hiraku launch evaluate                                      # task entrypoint
hiraku launch evaluate --experiment logistic-baseline       # task entrypoint for an experiment
hiraku launch logistic-baseline:train                        # experiment entrypoint
hiraku launch evaluate --task heart-disease                  # entrypoint in an explicit task
```

The task is selected by `--task`, then `HIRAKU_TASK_ID`, the local context, and finally the current task branch. The experiment context is selected by `--experiment`, then by the experiment in an `<experiment-id>:<entrypoint>` target, `HIRAKU_EXPERIMENT_ID`, and finally the local context. Explicit options are always overrides. This means a task entrypoint can run for an experiment, and an experiment entrypoint can be reused in a different experiment context. The selected experiment supplies the bound commit and receives the resulting run attribution. Without an experiment context, a task entrypoint runs without experiment attribution.

The positional target still determines where the entrypoint definition comes from: `<entrypoint>` resolves only task entrypoints, while `<experiment-id>:<entrypoint>` resolves only entrypoints defined by that experiment. Hiraku does not merge the namespaces or fall back between them, and both scopes may use the same name.

Launch the current repository state directly as a non-reproducible debugging execution:

```bash
hiraku launch logistic-baseline:train
```

For a reproducible launch, execute an exact commit in an isolated temporary Git worktree:

```bash
hiraku launch --commit HEAD logistic-baseline:train
hiraku launch --commit <commit-sha> logistic-baseline:train
```

Flags after the entrypoint are appended to the fixed command. E.g.,

```bash
hiraku launch logistic-baseline:train --seed 7
```

`hiraku launch`:

1. resolves the task, optional experiment context, and tracking configuration;
2. runs the current working tree as a non-reproducible source unless `--commit` is supplied;
3. for `--commit`, resolves the ref and creates a temporary detached worktree containing the exact committed snapshot;
4. resolves the selected task or experiment entrypoint, fixed argv, safe project-relative working directory, and environment from the selected source tree;
5. injects the task ID, optional experiment ID, source commit, launch provenance, backend type, and fixed destination `tasks/<task-id>/runs.csv` in the original project into the child environment;
6. executes the command directly without a shell and propagates its exit status; and
7. removes a temporary worktree without changing the active checkout.

The integration environment includes:

```text
HIRAKU_TASK_ID
HIRAKU_EXPERIMENT_ID  # set when the launch has an experiment context
HIRAKU_SOURCE_COMMIT
HIRAKU_TRACKING_BACKEND
HIRAKU_TABLE_PATH
```

When an experiment context is selected, `HIRAKU_EXPERIMENT_ID` contains its stable experiment slug. `HIRAKU_SOURCE_COMMIT` contains the resolved implementation SHA. A reproducible experiment launch uses the selected experiment's bound source commit; an explicitly supplied `--commit` must resolve to the same SHA. Working-tree launches remain available for debugging but are non-reproducible and do not establish a bound experiment. Hiraku deliberately does not require a row to appear after a successful child process; the launcher remains a thin local wrapper rather than a scheduler or tracking SDK.

The launcher is a thin, customizable, local command wrapper, not a scheduler or job-dispatch system.

### Inspect experiments, runs, and leaderboards

```bash
hiraku experiment list
hiraku experiment list heart-disease
hiraku runs
hiraku runs heart-disease
hiraku runs heart-disease --sort val/log_loss --ascending
hiraku runs heart-disease --sort val/accuracy --descending
hiraku runs heart-disease --metrics val/log_loss,val/accuracy
hiraku runs heart-disease --verbose
hiraku runs heart-disease --json
hiraku runs heart-disease --all  # MLflow only
```

Experiment and run listings are always scoped to one task, supplied explicitly or inferred from the current task branch. Cross-task listing is not part of the PoC. Experiment listings read the local YAML and Markdown registry and enrich each record with derived launches, runs, metrics, and Git parents. The human experiment table includes each recorded branch. A `*` prefix marks experiments whose exact bound commit is contained in the task branch, followed by a legend naming that branch. This ancestry marker is a Git integration fact kept separate from the semantic `decision`; an accepted experiment without the marker emits a warning. Unbound, missing, squashed, or cherry-picked commits receive no marker because exact ancestry cannot establish their integration.

The default human run table is a compact review view. It shows abbreviated run and commit IDs, experiment, timestamp, status when available, useful MLflow parameters, and selected metrics. It omits the redundant task ID, nested backend objects, MLflow system tags, and a false commit-mismatch field. `--verbose` shows complete flattened metadata without duplicating the nested `params`, `metrics`, and `tags` objects. Human-readable commit hashes use ten characters throughout the CLI; stored records and JSON retain exact hashes.

Metric columns come from the union of metric keys in the displayed runs. MLflow uses only native MLflow metrics; numeric parameters and tags are never inferred as metrics. For table tracking, non-provenance columns are result columns because the format does not distinguish parameters from metrics. `--metrics name,other` explicitly selects and orders human table columns. When task front matter declares `metrics`, those metrics are the default columns in declaration order and the first is the primary ranking metric. Its `minimize` or `maximize` direction supplies the default ranking direction. An explicit `--sort` may omit `--ascending` or `--descending` only when that metric has a declared direction; explicit direction flags override the declaration. Without declared or explicitly selected metrics, keys are alphabetical and the compact view shows at most six, with a warning directing the user to `--metrics` or `--verbose`.

Rows with missing or non-numeric sort values remain visible after ranked rows. JSON remains complete and lossless regardless of compact human column selection so the agent skill can consume it without scraping terminal formatting.

CSV/TSV source columns and string values are preserved. Sorted JSON rows receive an ordinal
`_rank`; rows with missing or non-numeric metrics have no rank.

The table backend requires each run producer to record a UTC ISO-8601 `created_at` timestamp. Hiraku injects launch context but does not invent or append the run row, so timestamp creation remains the responsibility of the tracking backend or training script.

### Open the experiment dashboard

Install the dashboard extra, then open a strictly read-only dashboard for an explicit or current
task:

```bash
uv sync --extra dashboard
hiraku dashboard heart-disease
```

The command starts a local server on `127.0.0.1` using an available port, opens the browser, and runs
until interrupted. `--no-open` prints the URL without opening a browser; `--host` and `--port` allow
explicit overrides. A manual refresh reloads task, experiment, Git, and tracking data without file
watching or polling.

The dashboard renders `task.md`, experiment and run tables, Git-derived lineage for branched
experiments, and an Altair/Vega-Lite metric timeline. Its performance frontier is the cumulative
minimum or maximum according to the selected task metric. Experiment and experiment-status filters
update the run table and chart and recompute the frontier from visible numeric runs. Run execution
status remains metadata rather than a separate filter.

All browser assets are pinned and bundled for offline use. The dashboard never edits records, run
data, Git state, or reviews. Table dashboards require `hiraku[dashboard]`; MLflow dashboards require
both the `dashboard` and `mlflow` extras.


### MLflow tracking backend

MLflow is an optional installation extra. It uses a local SQLite database at
`<project-root>/mlflow.db` by default; a safe project-relative `mlflow_tracking_db` overrides it.
Alternatively, `mlflow_tracking_uri` selects a remote HTTP(S) tracking server. The local override and
remote URI are mutually exclusive. Each task specifies `mlflow_experiment` in `task.md` front matter.

```yaml
tracking_backend: mlflow
mlflow_tracking_db: mlflow.db  # optional
```

For remote tracking, omit `mlflow_tracking_db`:

```yaml
tracking_backend: mlflow
mlflow_tracking_uri: https://mlflow.example.org
```

Hiraku passes a remote URI unchanged to launches and run inspection. Authentication and TLS use
standard MLflow environment variables; credentials must not be embedded in `.hiraku.yml`. Remote
errors never fall back to local SQLite. Hiraku does not provision or administer the server.

Hiraku constructs an absolute `sqlite:///` tracking URI for the database under the original project root. Committed launches therefore write to the same database even though their source executes from a detached temporary worktree.

Hiraku uses MLflow's native run ID and start time as `run_id` and `created_at`. User code records `task_id`, semantic `experiment_id`, and `commit` as MLflow parameters. JSON keeps `params`, `metrics`, and `tags` namespaced. For a metric logged several times, run inspection uses MLflow's returned latest-timestamp value; metric history is not part of Stage 2. By default, listings show only runs with all required provenance and a matching task ID, while warning about omitted runs. The MLflow-only `--all` option reveals invalid, wrong-task, or unassociated runs with explicit warnings and leaves runs without numeric sort values unranked.

MLflow launches receive `HIRAKU_MLFLOW_TRACKING_URI`, `HIRAKU_MLFLOW_EXPERIMENT`, `MLFLOW_TRACKING_URI`, and `MLFLOW_EXPERIMENT_NAME` instead of `HIRAKU_TABLE_PATH`. Both backends continue to receive the common task, experiment, commit, and backend variables.



### Running the examples

Each example is a copyable project. Copy it outside the Hiraku checkout before initializing it;
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

The interactive [`examples/heart-disease`](examples/heart-disease) flow is the main tutorial example. The same task can use the `autoresearch` program for a bounded CPU-only autonomous loop.
[`examples/smoke`](examples/smoke) is only the shortest installation and tracking-contract check.

Stage 2 also includes [`examples/autoresearch`](examples/autoresearch), pinned to upstream commit `228791fb499afffb54b46200aca536f79142f117`. It vendors the upstream GPU training and preparation code with MIT attribution, closely adapts the original autonomous program to Hiraku tasks, experiments, and runs, lets the user choose `revert`, `reset`, or retained-experiment-`branch` history, and provides a CPU-only contract entrypoint for automated workflow validation.



### Open Questions
- `hiraku run list` is missing, inconsistent with `hiraku experiment list` / make the CLI more consistent?
- use refs instead of branches if user doesn't want to clutter their branch space.
- tag / ref root nodes?
- use jj as branching backended - optionally.

### Deferred

> Features listed here are considered out-of-scope for the proof of concept, but should be taken into account for later iteration.

- **Research planning and autonomy**
  - An outer loop for brainstorming or autonomously creating several task scopes.
  - Inner-loop exploration strategies.
  - Enforced program boundaries and integration with sandboxing.
  - Standardized capture of agent conversations, traces, chain-of-thought, or tool-call histories.

- **Lineage and project interface**
  - Synchronization, backup, or remote hosting of the local experiment registry.

- **Execution and tracking integrations**
  - HPO and scheduler integration around named entrypoints.
  - Additional run-tracking adapters, such as W&B.

## Out of scope

> Features listed here are not considered part of the Hiraku framework.

- Versioning of datasets, benchmarks, environments, or evaluation protocols is up to the user; these can be specified in code or referenced in agent instructions.
