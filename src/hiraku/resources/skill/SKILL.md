---
name: hiraku
description: Support data science and machine learning research in Hiraku projects by managing tasks and experiments, launching entrypoints, querying tracked runs, recording findings, and execute research workflows.
---
# Hiraku

Hiraku connects research goals, experiments, source code, execution, and findings.
Use it to support the user's research at whatever stage they are working on.
Choose the relevant operations below; they are not a prescribed workflow. Keep
records useful and proportional to the work, using the project's own conventions.

## Framework

- **Task:** a research scope, goal, and evolving account of findings, stored in
  `hiraku/tasks/{task_id}/task.md`.
- **Experiment:** a focused investigation within a task, stored in
  `hiraku/tasks/{task_id}/experiments/{experiment_id}.md`. It can represent a
  baseline, hypothesis, diagnostic, ablation, or other investigation. A record
  without a source commit is a **proposal**; binding a commit links it to code.
  Several experiments can share a commit and differ by entrypoint arguments.
- **Run:** an execution trace owned by CSV, MLflow, W&B, or a custom tracking
  backend. Hiraku queries runs; the training or evaluation code logs them directly to the tracking backend.

Records are Markdown with YAML front matter. Core fields have validated meaning;
additional fields such as `hypothesis`, `result`, and `status` are user metadata,
not an enforced state machine. Preserve existing fields and prose when editing.
`hiraku.yml` configures the project and can relocate the `hiraku` directory.
Metadata may be committed or untracked; Git manages code history and merging.

## Inspect and maintain research records

Use `hiraku status --json` to discover the project and active selections. Inspect
relevant records and prior results to ground the work. Most record commands
accept `--json`; use command-specific `--help` for exact options.

```bash
hiraku task list
hiraku task show [task-id]
hiraku experiment list --task <task-id>
hiraku experiment show <experiment-id> --task <task-id>
hiraku proposal list

hiraku task create <task-id> --goal "..." --question "..."
hiraku experiment create <experiment-id> --hypothesis "..."
hiraku experiment update <experiment-id> --commit HEAD
hiraku experiment update <experiment-id> --set 'result="..."'
hiraku task update <task-id> --summary "..."
```

Square brackets above indicate an optional argument. `experiment` also has the
alias `exp`. Creating tasks and experiments activates them by default; `--no-set`
avoids that. Switch with `task set` or `experiment set`; `experiment clear`
clears the active experiment. Explicit task selection is useful when comparing
or updating work outside the active task.

Tasks and experiments support `create`, `list`, `show`, `update`, `edit`, and
`delete`. Use repeatable `--set 'key=<YAML value>'` for custom metadata and
`--unset key` to remove fields. `--markdown-file path` replaces the Markdown body,
so retain relevant existing notes. Record hypotheses as hypotheses and link
conclusions to actual run IDs, metrics, and source commits. Missing run attribution
is unknown, not evidence that a run belongs to the active experiment.

## Entrypoints and source code

Tasks and experiments can declare named entrypoints in front matter:

```yaml
entrypoints:
  train:
    command: [uv, run, python, train.py]
    args: {seed: 42, epochs: 40}
```

```bash
hiraku experiment entrypoints <experiment-id>
hiraku launch <experiment-id>:train -- --seed 7
hiraku launch --task <task-id> train
hiraku launch --dispatch worktree --commit <ref> train
```

Hiraku options may appear around the target before `--`; everything after
`--` overrides entrypoint arguments. Undeclared arguments are accepted with a
warning. A bare target selects a task entrypoint with task-only attribution. An
`<experiment-id>:train` selector checks the experiment first, inherits the task
definition when absent, and always attributes the run to that experiment. Active
experiment state never affects launch.

The project owns its dispatch scripts. `launch.sh` runs the current state
without commit attribution; `worktree.sh` runs and attributes the exact commit
in a temporary worktree. An experiment selector supplies its bound commit unless
`--commit REF` overrides it. Optional experiment branches (`experiment create --branch
[BRANCH] --from REF`) support separate lines of investigation; ordinary Git
operations manage commits and merges.

Dispatchers inject available `HIRAKU_TASK_ID`, `HIRAKU_EXPERIMENT_ID`,
`HIRAKU_ENTRYPOINT`, and task/experiment file paths. `HIRAKU_COMMIT` is present
only when the dispatcher guarantees exact commit provenance, as `worktree.sh`
does. Commands are argument lists, not shell expressions. `--dispatch NAME`
selects `hiraku/dispatch/NAME.sh` (the `.sh` suffix is optional); the configured
default is `launch.sh`. Child stdout and stderr stream live to the terminal.

## Logging and comparing runs

Select a backend through the project's `tracking_backend` mapping or a task
override, for example `--set 'tracking_backend={type: mlflow}'`.

- **CSV (default):** write task-local `runs.csv`, with `id`, attribution columns
  (`task_id`, `experiment_id`, `commit`, `timestamp`, `entrypoint`), and
  `param.<name>` / `metric.<name>` columns. The injected task file path can locate
  the original task directory even from a worktree.
- **MLflow:** log provenance as `hiraku.task_id`, `hiraku.experiment_id`,
  `hiraku.commit`, and `hiraku.entrypoint` tags, plus native parameters and
  metrics. Use `step` for learning curves. Configure the logging script and
  Hiraku adapter to use the same tracking URI and MLflow experiment. The adapter
  defaults to project-root `mlflow.db`; V1's launcher does not automatically set
  MLflow environment variables. An absolute SQLite URI keeps worktree launches
  pointed at the same database.
- **W&B:** log the same `hiraku.*` attribution keys in run config, plus parameters
  and metrics, using the project/entity configured for the adapter.

```bash
hiraku run list --task <task-id> --json
hiraku run list --experiment <experiment-id> \
  --params seed,epochs --metrics val/log_loss,val/accuracy \
  --sort val/log_loss --ascending
```

Hiraku exposes normalized run summaries; full curves and artifacts stay in the
tracking backend. Compare compatible data splits, metrics, and budgets, then
capture useful findings in experiment or task records as the research develops.

## Other V1 interfaces

The Python API provides `HirakuConfig` in `hiraku.config`, and `Task`,
`Experiment`, `Entrypoint`, `Metric`, `Run`, record loaders, and listing functions
in `hiraku.data`. Tasks and experiments expose `.runs()`; `hiraku.launch.launch`
executes entrypoints. Use these for scripted analysis or record management.

`hiraku context`, `hiraku action`, and `hiraku program` provide record management
commands for supporting material and instructions. Consult these when relevant
to the user's request. V1 does not provide a `hiraku-agent` runner or enforce
program execution, checkpoints, or research loops.
