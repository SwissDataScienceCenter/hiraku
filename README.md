# Hiraku

**Hiraku** is a toolkit for AI-assisted data science.

The Hiraku core defines customizable workflows around the model development lifecycle of data science and machine learning projects. A shared metadata record enables both interactive human–AI collaboration and autonomous research, with explicit boundaries and checkpoints for reviewing and verification. 

The toolkit provides deterministic support functions via a command-line interface (`hiraku`) and agent integration with the `/hiraku` skill, hiraku agent (`hiraku-auto`), harness (tbd), and sandboxing (tbd).

**Why Hiraku?**
In renku (連句), the traditional Japanese art of collaborative linked poetry, hiraku (平句) are the regular verses where the sequence develops, shifts, and explores new directions — a  metaphor for iterative, exploratory research within the Renku ecosystem.


## The Model-Development Lifecycle

In the experimental phase of a data science project, the problem, data, and objective are typically well defined, and the focus shifts to iterative model development (cf. [CRISP-ML](https://ml-ops.org/content/crisp-ml)). For a given task with a defined goal, data scientists typically cycle through a sequence of steps:

1. defining an experiment (e.g., a baseline, hypothesis or diagnostic),
2. implementation and debugging,
3. executing runs and tracking metrics,
4. analyzing and comparing results, and
5. iterating or concluding.

### The Problem: Metadata tracking

While standard tools support individual stages of this cycle (e.g., Git for version control, Weights & Biases or MLflow for experiment tracking, and notebooks for analysis, etc), the meta-level information that connects these stages is often only loosely documented.

This fragmentation creates an inherent barrier for effective human-AI collaboration: proposals, hypotheses, implementation, experiments, diagnostics, results, and conclusions are not consistently linked or documented as part of a coherent research process. Without a lightweight but explicit protocol for capturing the research process, AI systems cannot reliably inspect prior work, understand why decisions were made, propose meaningful next steps, to effectively support data scientists through iterative experimentation. Similarly, without a metadata trace, the data scientist cannot track, inspect and verify the output of an autonomous research agent.


## Hiraku core

Hiraku addresses this gap by linking metadata, workflows and agent execution. 

### Data model: Tasks, experiments, runs

Hiraku uses three lightweight data structures for tracking workflows: tasks, experiments, runs. From these, only tasks and experiments are managed directly by Hiraku. Runs are owned by a user-managed tracking backend, and are queried via a backend interface.

A *task* defines a working scope and research goal. *Experiments* are smaller units that link a task, hypothesis, code, and results. Tasks and experiments may each define execution entrypoints. A *program* defines an agent workflow and its boundaries. *Runs* record execution traces for a specified task or experiment and code commit.


| Term | Data | Summary |
| --- | ---  | --- |
| **Task** | `hiraku/tasks/<task-id>/task.md`| A task is the top-level Hiraku abstraction that defines the scope of a research effort. A task defines any of the following: a research question, a goal, metrics and records scientific documentation of the research task, including scope, methodology, and results. |
| **Proposal / Experiment** | `hiraku/tasks/<task-id>/experiments/<experiment-id>.{md,yml}` | An *experiment* is a focused investigation within a task that refers to a source commit and may define execution entrypoints. An experiment without a reference commit is called a *proposal*. Its intent is user-defined: it can be a baseline, hypothesis, diagnosis, model run, ablation, or anything else; Hiraku does not impose specific semantics. A typical experiment lifecycle spans proposal, implementation, execution, analysis, and a record of the findings. The experiments associated with a task provide an overview of the research effort. |
| **Run** | (backend owned execution trace) | A single execution trace of an experiment. The tracking backend and logging is user managed by the user, Hiraku only queries results via compatibility backend. Example backends: CSV, W&B, MLFlow, custom user backend.  |



Hiraku data records are managed by the Python Core API. 
```python
from hiraku.core import Task, Experiment, Run
```

The CLI provides a standardized (~RESTful) interface, .e.g
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

Tasks and experiments may define named **entrypoints** that are used to launch runs. These can be invoked via the CLI:
```bash
hiraku launch <entrypoint>  # task entrypoint
hiraku launch --task <task-id> <entrypoint>  # task defaults to active task
hiraku launch --experiment <exp-id>  <entrypoint>  # defines experiment context, not task selection, defaults to the active experiment. `--no-exp` launches without experiment (tbc, confusing?)
hiraku launch <entrypoint>  --seed 1  # arguments after the entrypoint are passed to the launch script (tbc, confusing? forces hiraku args to be before the entrypoint...)
hiraku launch <exp-id>:<entrypoint>
hiraku launch  --experiment <other-exp-id>  <exp-id>:<entrypoint> # launches <exp-id> with a forced <other-exp-id context>
```


The user can also define dispatch scripts, e.g.
```bash
hiraku launch <exp-id>:<entrypoint> --dispatch runai
```
This invokes the dispatch script `hiraku/dispatch/runai.sh` with the full launch command after a `--` separator. The dispatch script can be used to implement custom launch behavior, e.g., for a specific cluster or job scheduler, launch event logging, etc.

A **tracking backend** is a compatibility interface that provides a uniform API for querying and listing runs. Hiraku ships with three backends: CSV, MLflow, and W&B. Users can implement their own backends.


### Agent integration: Programs, Actions, Contexts

Hiraku provides agent integration with programs, actions and context. A *program* defines a research workflow executed by the agent, with defined permissions and limits. *Actions* define one-step execution patterns that combine deterministic hooks with LLM instructions. Hiraku ships default programs and actions, and these can be customized by the user. *Context* files can be used to summarize information that is used by the agent.


| Term | Data | Summary |
| --- | ---  | --- |
| **Program** | `hiraku/programs/<program-id>/program.md` | A named research workflow that defines agent instructions, permissions, resource limits, checkpoints, and stopping conditions. |
| **Action** | `hiraku/actions/<entity>/<action-id>/action.md` | A named action that can be invoked by the hiraku-auto agent. |
| **Context** | `hiraku/contexts/<context-id>/context.md` or `hiraku/context/<context-id>.md` | Curated context files, e.g. literature, cross-task summaries, etc. that can be referenced. |

Programs, action and context are managed via CLI, e.g.
```bash
hiraku program create <program-id>
hiraku program list
hiraku context create <context-id>
hiraku context list
hiraku action create <entity>/<action-id>
hiraku action list
```

The `hiraku-auto` provides direct agent invocation for actions and programs, with a configured provider (e.g., Codex, Pi, Claude, SDSC models, etc), in particular
```bash
hiraku-auto <entity> <action-id>   # invokes hiraku/actions/<entity>/<action-id>/action.md, e.g.
hiraku-auto experiment accept  # invokes hiraku/actions/experiment/accept/action.md
hiraku-auto experiment accept --experiment <exp-id> # invokes hiraku/actions/experiment/accept/action.md
hiraku-auto experiment propose  --num 5 # invokes hiraku/actions/experiment/propose/action.md
```

The `\hiraku` skill can be used directly in a coding harness. Later, we might add a custom harness (e.g, Pi extension) that enforces workflow steps and boundaries defined by programs or actions.

  > ToDo: Define how programs are invoked.

  > Note: Hiraku is agnostic to git state of meta data as far as possible. The user my commit the metadata files to to git repository or leave `hiraku/*` untracked. Hiraku simply lists what is currently visible in the working directory, or tries to detect md files on the defined task branch. The recommended pattern is to commit `hiraku/*`, however merging and conflict resolution is a user (or agent) responsibility.
  
  > Note: All records (tasks.md, experiment.md, program.md, context.md, action.md) share the same underlying data interface: Markdown with yaml frontmatter. Markdown is optional in which case the file can be saved as yaml (e.g., experiment.yml). Hiraku only validates core fields that carry semantic meaning (e.g. id, referenced ids, etc), and accepts arbitrary user fields, used for summarization or with agent actions. Default lookup paths are defined in the config and can be customized by the user.


## Example workflow
The following sequence illustrates the intended workflow. It can be run step-by-step or autonomously within the task program's boundaries. The data scientist can review the mutable experiment record at any point; reviewed conclusions are later summarized in `task.md` and, in stage 2, in a pull request or merge request.

```bash
# Setup
hiraku init  # creates Hiraku project configuration in `.hiraku.yml`
hiraku status  # show current status and config

# 1. create task
hiraku task create heart-disease  # creates and sets the active task
/hiraku "define the fixed heart-disease evaluation protocol"  # agent-assisted workflow
hiraku task show heart-disease  # task details, --json returns machine-readable output

hiraku status  # show current active task

# 2. create an experiment, then implement and bind it
hiraku experiment create logistic-baseline --summary "Logistic regression baseline" --hypothesis "Regularized logistic regression provides a calibrated baseline" --entrypoint logistic  # creates and sets the active experiment
hiraku status  # show current active experiment
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

#### Simple workflow
The user creates a task and associated working branch; `main` is a valid branch. The simplest, default workflow is linear: create a new experiment, implement and commit, bind the record, launch runs, and update the record. Implementation changes can be reverted or reset using the standard git actions. The task-level `task.md` synthesizes the reviewed findings. There is no branching or parallel work.

#### Advanced workflows with branching
Experiment branches can be used to explore several ideas in parallel or a task can backtrack to a prior branch or commit. A branched experiment is created with `hiraku experiment create ... --branch [--from <ref>]`. The default branch is `hiraku/experiment/<task-id>/<experiment-id>`, and the selected branch is recorded in the experiment record. `--from` may resolve another branch or commit and defaults to task-branch HEAD. Without `--branch`, the experiment remains on the task branch.

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
- [Note: Renku Naming Conventions](docs/renku.md)

### User Stories
- [Matteo-simple](docs/stories/Matteo-simple.md)
- [Matteo-advanced](docs/stories/Matteo-advanced.md)
- [Qianqian](docs/stories/Qianqian.md)
- [Johannes-simple](docs/stories/Johannes-simple.md)
- [Johannes-advanced](docs/stories/Johannes-advanced.md)

