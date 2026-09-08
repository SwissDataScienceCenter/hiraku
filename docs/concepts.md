# Essential concepts

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

> [!NOTE] 
> Hiraku is agnostic to git state of metadata as far as possible. The recommended workflow is to commit the metadata records in `hiraku/*`. When switching branches, Hiraku simply lists what is currently visible in the working directory. For advanced workflows using multiple branches, merging and conflict resolution is a user (or agent) responsibility.

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


Hiraku keeps the research description close to the code that evaluates it.

| Concept | Meaning |
| --- | --- |
| **Task** | The research goal, question, metrics, and permitted entrypoints. |
| **Experiment** | One concrete approach and its hypothesis. It can be bound to a source commit. |
| **Run** | One execution of an experiment, with its metrics, parameters, and provenance. |
| **Entrypoint** | A named command that Hiraku can execute. |

A proposal is an Experiment that does not have a committed source yet. Once an
Experiment is bound to a commit, Hiraku can launch it and associate Runs with
that exact source state.

The task record describes *what to investigate*. The experiment record describes
*which approach to try*. The run describes *what happened when it ran*.

Runs are read from the tracking backend; the training program must log them.
Hiraku does not create a run record just because a command executes.

See the [Python API](api/index.md) for validated model fields and the [CLI
reference](api/cli.md) for exact command options.
