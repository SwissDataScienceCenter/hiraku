# Hiraku

**Hiraku** is a toolkit for AI-assisted data science.

The Hiraku core defines customizable workflows around the model development lifecycle of data science and machine learning projects. A shared metadata record enables both interactive human–AI collaboration and autonomous research, with explicit boundaries and checkpoints for reviewing and verification. 

The toolkit provides deterministic support functions via a command-line interface (`hiraku`) and agent integration with the `/hiraku` skill, hiraku agent (`hiraku-agent`), harness (tbd), and sandboxing (tbd).

**Why Hiraku?**
In renku (連句), the traditional Japanese art of collaborative linked poetry, hiraku (平句) are the regular verses where the sequence develops, shifts, and explores new directions — a  metaphor for iterative, exploratory research within the Renku ecosystem.


## The Model-Development Lifecycle

In the experimental phase of a data science project, the problem, data, and objective are typically well defined, and the focus shifts to iterative model development (cf. [CRISP-ML](https://ml-ops.org/content/crisp-ml)). For a given task with a defined goal, data scientists typically cycle through a sequence of steps:

1. defining an experiment (e.g., a baseline, hypothesis or diagnostic),
2. implementation and debugging,
3. executing runs and tracking metrics,
4. analyzing and comparing results, and
5. iterating or concluding.

While standard tools support individual stages of this cycle (e.g., Git for version control, Weights & Biases or MLflow for experiment tracking, and notebooks for analysis, etc), the meta-level information that connects these stages is often only loosely documented.

This fragmentation creates an inherent barrier for effective Human-AI collaboration: proposals, hypotheses, implementation, experiments, diagnostics, results, and conclusions are not consistently linked or documented as part of a coherent research process. Without a lightweight but explicit protocol for capturing the research process, AI systems cannot reliably inspect prior work, understand why decisions were made, propose meaningful next steps, to effectively support data scientists through iterative experimentation. Similarly, without a metadata trace, the data scientist cannot track, inspect and verify the output of an autonomous research agent.

## Data model: Tasks, experiments, runs

Hiraku uses three lightweight data structures: tasks, experiments, runs. From these, only tasks and experiments are managed directly by Hiraku. Runs are owned by a user-managed tracking backend  (e.g., Weights & Biases, MLflow, CSV, etc), and are queried via a backend interface.

A *task* defines a working scope and goal, and documents the research progress. *Experiments* are smaller units that link a task, code, execution entrypoints, and runs. Experiments carry no specific semantics beyond this role; the user may create experiments to implement baselines, hypotheses, diagnostics, models runs, ablations, hyperparameter optimization, etc. *Runs* record execution traces for a specified task or experiment and code commit.

| Term | Data | Summary |
| --- | ---  | --- |
| **Task** | `hiraku/tasks/{task_id}/task.md`| A task is the top-level Hiraku abstraction that defines the scope of a research effort.  |
| **Proposal / Experiment** | `hiraku/tasks/{task_id}/experiments/{experiment_id}.md` | An *experiment* is a focused investigation within a task that refers to a source commit and execution entrypoints. An experiment without a reference commit is called a *proposal*. |
| **Run** | backend owned execution trace | A single execution trace with (optional) task, experiment, and commit attribution. The tracking backend and logging is user (or agent) managed; Hiraku only queries results via a compatibility backend.|


At any time, a single task and experiment can be set as "active". By default, "create" actions activate the new task or experiment. The user can switch explicitly with `hiraku task set <task-id>` or `hiraku experiment set <experiment-id>`. The current status can be queried with `hiraku status`.


### Python Core API

Hiraku data records are managed by the Python Core API. 
```python
from hiraku.data import Entrypoint, Experiment, Metric, Run, Task
```

### CLI
The CLI provides a standardized (~RESTful) interface for managing data records, .e.g
```bash
hiraku task create <task-id>
hiraku task list
hiraku task list --columns task_id,question,goal  # list user-defined fields
hiraku task delete <task-id>
hiraku task show

hiraku exp create <exp-id>
hiraku exp list
hiraku exp list --columns exp_id,created_at,hypothesis,commit,status,result --sort created_at --ascending # list user-defined fields
hiraku exp update <exp-id> --commit <hash>
hiraku exp delete <exp-id>

hiraku proposal list  # short for "hiraku exp list --filter commit==None --sort created_at --descending"

hiraku run list
hiraku run list --columns task_id,exp_id,timestamp,commit --params seed,lr --metrics train/accuracy,val/accuracy,train/cross_entropy,val/cross_entropy --sort val/accuracy --ascending  # custom metric fields
hiraku run list --sort val/accuracy --ascending
```

### Entrypoints

Tasks and experiments may define named **entrypoints** that are used to launch runs. Entrypoints are defined in the task or experiment YAML front matter with the `entrypoints` key, e.g.,
```yaml
entrypoints: 
  train:
    command: ["uv", "run", "python", "train.py"]
    args:
      model: logistic
      seed: 42
```
Entrypoints can be invoked via the CLI:
```bash
hiraku launch <entrypoint>  # task entrypoint
hiraku launch <exp-id>:<entrypoint>  # experiment entrypoint
hiraku launch --task <task-id> <entrypoint>  # task defaults to active task
hiraku launch --experiment <exp-id>  <entrypoint>  # defines experiment context, not task selection, defaults to the active experiment. `--no-exp` launches without experiment (tbc, confusing?)
hiraku launch  --experiment <other-exp-id>  <exp-id>:<entrypoint> # launches <exp-id> with a forced <other-exp-id context>
hiraku launch <entrypoint>  --seed 1  # arguments after the entrypoint are passed to the launch script (tbc, confusing? forces hiraku args to be before the entrypoint...)
```

Launches can be run from a specified commit in an isolated work tree using 
```bash
hiraku launch --commit <hash> <entrypoint> 
```

Hiraku injects environment variables into the launch, including `HIRAKU_TASK_ID`, `HIRAKU_EXPERIMENT_ID`, `HIRAKU_TASK_FILE`, `HIRAKU_EXPERIMENT_FILE`, ...

The user can define custom dispatch scripts, e.g.
```bash
hiraku launch <exp-id>:<entrypoint> --dispatch runai
```
This invokes the dispatch script `hiraku/dispatch/runai.sh` with the full launch command after a `--` separator. The dispatch script can be used to implement custom launch behavior, e.g., for a specific cluster or job scheduler, launch event logging, etc. By default, Hiraku calls the `hiraku/dispatch/launch.sh` script, which can be easily customized by the user, e.g. to active a custom environment, set environment variables, etc.


### Tracking Backend

A **tracking backend** is a compatibility interface that provides a uniform API for querying and listing runs. Hiraku ships with three backends: CSV, MLflow, and W&B. Users can implement their own backends. The backend needs to implement the following functions
- List runs associated to a given task
- Return normalized runs with a required run ID, metrics, and parameters; task ID, experiment ID, commit, timestamp, entrypoint, and URL attribution may be absent.

CSV is the default project backend. A task may set `tracking_backend` to
override the project default; MLflow and W&B are selectable alternatives.


## Agent integration: Programs, Actions, Context

Hiraku provides agent integration with actions, programs, and context. *Actions* define one-step execution patterns that combine deterministic hooks with LLM instructions.  A *program* defines a research workflow executed by the agent, with defined permissions and limits. Hiraku ships default programs and actions, and these can be customized by the user. *Context* files can be used to summarize information that is used by the agent.


| Term | Data | Summary |
| --- | ---  | --- |
| **Program** | `hiraku/programs/{program_id}/program.md` | A named research workflow that defines agent instructions, permissions, resource limits, checkpoints, and stopping conditions. |
| **Action** | `hiraku/actions/{entity}/{action_id}/action.md` | A named action that can be invoked by the hiraku-agent agent. |
| **Context** | `hiraku/contexts/{context_id}/context.md` | Curated context files, e.g. literature, cross-task summaries, etc. that can be referenced. |

Programs, action and context are managed via CLI, e.g.
```bash
hiraku program create <program-id>
hiraku program list
hiraku context create <context-id>
hiraku context list
hiraku action create <entity>/<action-id>
hiraku action list
```

> ToDo: Add some way of referencing one or multiple context files in a task, program etc. Add `hiraku context` to list the current context files that the agent would be given.

The `hiraku-agent` provides direct agent invocation for actions and programs, with a configured provider (e.g., Codex, Pi, Claude, SDSC models, etc), in particular
```bash
hiraku-agent <entity> <action-id>   # invokes hiraku/actions/{entity}/{action_id}/action.md, e.g.
hiraku-agent experiment accept  # invokes hiraku/actions/experiment/accept/action.md
hiraku-agent experiment accept --experiment <exp-id> --implement# invokes hiraku/actions/experiment/accept/action.md
hiraku-agent experiment implement --experiment <exp-id> # invokes hiraku/actions/experiment/accept/implement.md, experiment defaults to active experiment
hiraku-agent experiment propose  --num 5 # invokes hiraku/actions/experiment/propose/action.md
```

The `\hiraku` skill can be used directly in a coding harness. Later, we might add a custom harness (e.g, Pi extension) that enforces workflow steps and boundaries defined by programs or actions.

  > ToDo: Define how programs are invoked.

  > ToDo: Actions should have a deterministic component (e.g. event hooks). E.g., there might be a deterministic check before accept that the experiment is in proposal state, etc.

  > ToDo: Should we add a status as core field to experiments? Then we can have action enforce a state transition, e.g., from "proposed" to "implemented" etc.

  > Note: Hiraku is agnostic to git state of meta data as far as possible. The user my commit the metadata files to to git repository or leave `hiraku/*` untracked. Hiraku simply lists what is currently visible in the working directory, or tries to detect md files on the defined task branch. The recommended pattern is to commit `hiraku/*`, however merging and conflict resolution is a user (or agent) responsibility.
  
  > Note: All records use Markdown with YAML front matter and a `.md` ending. The Markdown body may be empty. Hiraku only validates core fields that carry semantic meaning (e.g. id, referenced ids, etc), and accepts arbitrary user fields, used for summarization or with agent actions. `hiraku.yml` may relocate the common `hiraku_dir`; record paths follow a fixed layout beneath it.

### Default Programs and Actions

**Programs**
- `interactive`: A step-by-step program where the data scientist explicitly approves each checkpoint and reviews artifacts before proceeding.
- `autoresearch`: A bounded autonomous loop with explicit permissions, resource limits, and stopping conditions under which the agent implements, runs, analyzes, and iterates, exposing all artifacts for post-hoc human verification.

**Actions**
- `experiment propose`: Propose a new experiment within the active task.
- `experiment implement`: Implement the active experiment.
- `task create`: Create a new task
- `task update`: Update the active task.md


## Example workflows

The following sequence illustrates the intended workflow.

### Interactive workflow
```bash
# Setup and status
hiraku init 
hiraku status 

# 1. optionally, initialize context
hiraku context create literature 
hiraku-agent context update literature "Curated literature review for heart-disease prediction" 

# add literature to the active context
hiraku context set literature
# print files that would be added to the context
hiraku context

# 2. a) create task, with CLI
hiraku task create heart-disease  --goal "Develop models for heart disease prediction" # creates and sets the active task

# 2. b) create task with agent and provided context
hiraku-agent task create heart-disease --goal "Develop models for heart disease prediction" --context literature # creates and sets the active task

# 2. c) create task with agent skill inside coding harness (e.g, Claude, Codex, ...)
/hiraku "create a new task for heart-disease prediction"  

# query task record and Hiraku status
hiraku task show [heart-disease]
hiraku task show heart-disease --json  
hiraku status  # show current active task

# 2. create an experiment, then implement and bind it
hiraku experiment create logistic-baseline --summary "Logistic regression baseline" --hypothesis "Regularized logistic regression provides a calibrated baseline" --entrypoint logistic  # creates and sets the active experiment
hiraku status  # show current active experiment

# 2. a) implement the experiment with agent
hiraku-agent experiment implement logistic-baseline
# 2. b) implement the experiment with agent skill inside coding harness (e.g, Claude, Codex, ...)
/hiraku "implement logistic baseline experiment"

# commit code change
git commit -m "implement logistic baseline"
hiraku experiment update heart-disease:logistic-baseline --commit HEAD


# 3. launch named entrypoints and list runs (from CSV/MLFlow,or W&B backend)
hiraku launch logistic-baseline:train
hiraku runs heart-disease --sort val/log_loss --ascending

# 4. analyze the runs and update the local experiment record
hiraku-agent experiment update "conclude logistic-baseline and record the results"

# 5. summarize reviewed findings in tracked task documentation
hiraku-agent task update "summarize the reviewed experiments in task.md"
# or, with skill
/hiraku "summarize the reviewed experiments in task.md"
```

### Autoresearch workflow
> ToDo: The exact invocation of an agentic research loop (harness) or program.md (prompted loop), is still open to define exactly.

```bash
hiraku-agent task create heart-disease --goal "Develop models for heart disease prediction" --context literature

hiraku program show autoresearch
hiraku program show interactive

# invoke autoresearch program, via agent
hiraku-agent --program autoresearch --task heart-disease
hiraku-agent --program interactive --task heart-disease --max-experiments 5 --max-runs 20 --time-budget 2h

# invoke autoresearch program via skill
/hiraku "run the autoresearch program for heart-disease prediction task"
```


### Linear workflow and branching

#### Simple workflow
The user creates a task and associated working branch; `main` is a valid branch, the default is `hiraku/tasks/{task_id}`. Experiment implementation is commited to the task branch. Implementation changes can be reverted or reset using the standard git actions. There is no branching or parallel work.

#### Advanced workflows with branching
Experiment branches can be used to explore several ideas in parallel or a task can backtrack to a prior branch or commit. A branched experiment is created with `hiraku experiment create ... --branch [BRANCH] [--from REF]`. Without a value, `--branch` selects `hiraku/experiments/{task_id}/{experiment_id}`; a value selects any valid new or existing branch. The selected branch is recorded in the experiment. `--from` may resolve another branch or commit, defaults to task-branch HEAD, and is used only when creating a branch. Without `--branch`, the experiment remains on the task branch.

The user is responsible to merge experiment branches into the task branch, and resolving conflicts. In particular, experiment records should be committed to the task branch so that they can be listed by Hiraku.

Experiment lineage is a DAG derived from Git commit history.
> Tbc: the advantage is that experiments point to a single commit so the experiment tree is always implicit without duplicating the tree structure; the disadvantage is that proposals are not part of the DAG, and commits without a ref may be lost unless ref'ed, the alternative is to add a parent_id to experiment records, either as core field, or user-managed field.

## Hiraku Documentation

- [Data Model](docs/data.md)
- [Agent Integration](docs/agent.md)
- [Tracking Backends](docs/tracking.md)
- [Python Core API](docs/core.md)
- [Command-Line Interface](docs/cli.md)
- [Installation and Configuration](docs/install.md)
- [Examples](docs/examples.md)
- [Note: Agent System Design](docs/design.md)
- [Note: Renku Naming Conventions](docs/renku.md)

### User Stories
- [Matteo-simple](docs/stories/Matteo-simple.md)
- [Matteo-advanced](docs/stories/Matteo-advanced.md)
- [Qianqian](docs/stories/Qianqian.md)
- [Johannes-simple](docs/stories/Johannes-simple.md)
- [Johannes-advanced](docs/stories/Johannes-advanced.md)
