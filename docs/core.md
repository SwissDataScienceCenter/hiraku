
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