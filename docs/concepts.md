# Hiraku concepts


## Experiment lifecycle

In the model development phase of a data science project, the data and objective are (relatively) well-defined, and the focus shifts to iterative model development (cf. [CRISP-ML](https://ml-ops.org/content/crisp-ml)). Data scientists typically cycle through a sequence of steps:

1. defining an experiment (e.g., a baseline, hypothesis or diagnostic),
2. implementation and debugging,
3. executing runs and tracking metrics,
4. analyzing and comparing results, and
5. iterating or concluding; revisit task goal if needed.

While standard tools support individual stages of this cycle (e.g., Git for version control, Weights & Biases or MLflow for experiment tracking, and notebooks for analysis, etc), the meta-level information that connects these stages is often only loosely documented.

This fragmentation creates an inherent barrier for effective Human-AI collaboration: proposals, hypotheses, implementation, experiments, diagnostics, results, and conclusions are not consistently linked or documented as part of a coherent research process. Without a lightweight but explicit protocol for capturing the research process, AI systems cannot reliably inspect prior work, understand why decisions were made, propose meaningful next steps, to effectively support data scientists through iterative experimentation. Similarly, without a metadata trace, the data scientist cannot track, inspect and verify the output of an autonomous research agent.

## Tasks, experiments, runs

Hiraku uses three lightweight data structures to track the model-development lifecycle: tasks, experiments, runs. 

Only tasks and experiments are managed directly by Hiraku. Runs are owned by a user-managed tracking backend  (e.g., Weights & Biases, MLflow, CSV, etc), and are queried via a backend interface.

A *task* defines a working scope and goal, and documents the research progress. *Experiments* are small, reproducible units within a task that link a code, execution entrypoints, and runs. Experiments carry no specific semantics beyond this role; the user may create experiments to implement baselines, hypotheses, diagnostics, models runs, ablations, hyperparameter optimization, etc. *Runs* record execution traces for a specified task or experiment and code commit.

| Term | Data | Summary |
| --- | ---  | --- |
| **Task** | `hiraku/tasks/{task_id}/task.md`| A task is the top-level Hiraku abstraction that defines the scope of a research effort.  |
| **Experiment** | `hiraku/tasks/{task_id}/experiments/{experiment_id}.md` | An *experiment* is a reproducible unit within a task that refers to a source commit and execution entrypoints. An experiment without a reference commit is called a *proposal*. |
| **Run** | backend owned execution trace | A single execution trace, typically a model training or eval run. The tracking backend and logging is user (or agent) managed; Hiraku only queries results via a compatibility backend.|

Tasks and experiments are stored as markdown with yaml frontmatter. This allows the user and agents to directly read and modify the data records.

At any time, a single task and experiment can be set as "active". By default, "create" actions activate the new task or experiment. The user can switch explicitly with `hiraku task set <task-id>` or `hiraku experiment set <experiment-id>`. The current status can be queried with `hiraku status`.

!!! Note
    Hiraku is agnostic to git state of metadata as far as possible. The recommended workflow is to commit the metadata records in `hiraku/*`. When switching branches, Hiraku simply lists what is currently visible in the working directory. For advanced workflows using multiple branches, merging and conflict resolution is a user (or agent) responsibility.


### CLI

The CLI provides a standardized interface for managing data records, .e.g
```bash
hiraku init  # initialize a Hiraku project and configuration

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

hiraku status # show current active task and experiment

hiraku proposal list  # short for "hiraku exp list --filter commit==None --sort created_at --descending"

hiraku run list
hiraku run list --columns task_id,exp_id,timestamp,commit --params seed,lr --metrics train/accuracy,val/accuracy,train/cross_entropy,val/cross_entropy --sort val/accuracy --ascending  # custom metric fields
hiraku run list --sort val/accuracy --ascending
```

### Python API

Hiraku data records are managed by the [Python Core API](docs/core.md). All other user interfaces (CLI, dashboard, agent harness, ...) should refer to the Python API wherever possible. 
```python
from hirak.records import Record
from hiraku.data import Entrypoint, Experiment, Metric, Run, Task
```


## Tracking Backend

A **tracking backend** is a compatibility interface that provides a uniform API for querying and listing runs. Hiraku ships with CSV, Weights and Biases, and MLflow adapters; additional adapters can be registered by users. The backend needs to implement the following functions
- List runs associated to a given task
- Return normalized runs with a required run ID, metrics, and parameters; task ID, experiment ID, commit, timestamp, entrypoint, and URL attribution may be absent.

CSV is the default project backend. A task may set `tracking_backend` to
override the project default. Backend configuration uses one common mapping
shape, with `type` identifying the adapter:

```yaml
tracking_backend:
  type: csv
```

## Entrypoints

Tasks and experiments can define named **entrypoints** for launching runs. Entrypoints document reproducible code invocations, including default hyperparameters or command-line arguments, and provide convenient functions for users and agents to run or dispatch code.

Entrypoints are defined in the task or experiment YAML front matter with the `entrypoints` key, e.g.,
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
hiraku launch --experiment <exp-id>  <entrypoint>  # defines experiment context for a task-level entrypoint
hiraku launch <entrypoint>  --seed 1  # arguments after the entrypoint are passed to the launch script
```

> [!IMPORTANT]
> Arguments that come after the entrypoint name are passed to the launch script. Hiraku options must come before the entrypoint name (tbd if practical)

Launches can be run from a specified commit in an isolated work tree using 
```bash
hiraku launch --commit <hash> <entrypoint> 
```

Hiraku injects environment variables into the launch, including `HIRAKU_TASK_ID`, `HIRAKU_EXPERIMENT_ID`, `HIRAKU_TASK_FILE`, `HIRAKU_EXPERIMENT_FILE`, ...

The user can define custom dispatch scripts, e.g.
```bash
hiraku launch --dispatch runai <exp-id>:<entrypoint>
```
This invokes the dispatch script `hiraku/dispatch/runai.sh` with the full launch command after a `--` separator. The dispatch script can be used to implement custom launch behavior, e.g., for a specific cluster or job scheduler, launch event logging, etc. By default, Hiraku calls the `hiraku/dispatch/launch.sh` script, which can be easily customized by the user, e.g. to active a custom environment, set environment variables, etc.



## Agent Skill

The `\hiraku` skill can be used directly in any coding harness. See [SKILL.md](src/hiraku/resources/skill/SKILL.md).

## Actions, Programs, Context

!!! Info
    Version 0.1.0 only implements the data model and not yet the agent integration. See github issues for the planned features.

Hiraku manages agent integration with actions, programs, and context. *Actions* define one-step execution patterns that combine deterministic hooks with LLM instructions and have a validated start and end state.  A *program* defines complex research workflows and loops end-to-end, with defined permissions and limits. Actions and programs are executed by the `hiraku-agent`. Hiraku ships default programs and actions, and these can be customized by the user. *Context* files can be used to provide project-relevant information to the agent.


| Term | Data | Summary |
| --- | ---  | --- |
| **Action** | `hiraku/actions/{entity}/{action_id}/{action.md,action.py}` | A named action that can be invoked by the hiraku-agent agent. |
| **Program** | `hiraku/programs/{program_id}/{program.md,program.py}` | A named research workflow that defines agent instructions, permissions, resource limits, checkpoints, and stopping conditions. |
| **Context** | `hiraku/contexts/{context_id}/context.md` | Curated context files, e.g. literature, cross-task summaries, etc. that can be referenced. |

Programs, action and context records are managed via CLI, e.g.
```bash
hiraku program create <program-id>
hiraku program list
hiraku context create <context-id>
hiraku context list
hiraku action create <entity>/<action-id>
hiraku action list
```



## Example workflows

The following sequence illustrates the intended workflow.

### Interactive workflow
```bash
# Setup and status
hiraku init 
hiraku status 

# 1. optionally, initialize context (not implement yet)
hiraku context create literature 
hiraku-agent context update literature "Create a curated literature review for heart-disease prediction" 

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


# 3. launch named entrypoints and list runs (from CSV or MLflow backend)
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
> ToDo: The exact invocation of an agentic research loop (harness) or program.md (prompted loop) is still tbd.

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
