---
name: hiraku
description: Use Hiraku to structure and carry out data-science and machine-learning research from focused record or experiment work through bounded investigations and autonomous research workflows.
---
# Hiraku

Hiraku connects research goals, experiments, source code, execution, and findings.
Use it at the scope the user requested: a single record operation, a bounded
sequence of research steps, or an explicitly requested autonomous workflow.
Treat Hiraku as a research workspace rather than a closed command menu. Translate
the user's research intent into the appropriate combination of records, source
work, launches, evidence, and findings, including requests that enter midway
through an existing task or experiment lifecycle.

## Development version and feedback

Hiraku is under active development. Be transparent when its behavior,
instructions, or records do not support the requested workflow cleanly:

- Flag apparent bugs, inconsistencies, missing capabilities, and unclear or
  incomplete documentation to the user. Describe the expected behavior, the
  observed behavior, and the evidence that distinguishes a Hiraku problem from
  an experiment or repository failure.
- If continuing requires a workaround that affects scope, provenance, records,
  reproducibility, or results, explain its tradeoffs and agree on it with the
  user before applying it. Do not silently edit records or bypass attribution to
  conceal a limitation.
- Encourage the user to report reproducible problems at the
  [Hiraku issue tracker](https://github.com/SwissDataScienceCenter/hiraku/issues),
  and offer to prepare a concise reproduction and expected behavior. Open or
  update an issue only with the user's authorization.
- When a missing Hiraku feature would significantly simplify or strengthen the
  requested work, suggest the capability and discuss it with the user. With
  their authorization, create a feature request that explains the research use
  case and desired outcome rather than prescribing an unnecessarily narrow
  implementation.
- Treat missing, misleading, or opaque Hiraku instructions and documentation,
  including this skill, as feedback worth surfacing. Suggest a concrete
  documentation or skill improvement when it would prevent the same problem.

## Follow the user's scope

Classify the request before acting:

- **One action:** Do only the requested operation, verify it, report the result,
  and stop. For example, "create an experiment" does not also implement or run
  it, and "run this experiment" does not create another experiment or rewrite
  its record.
- **Bounded workflow:** Complete every requested stage in order, such as creating
  a task, defining two experiments, running them, and comparing their results.
  Do not add stages the user did not request.
- **Autonomous or program workflow:** Start or continue a research loop only when
  the user explicitly requests it. Read the selected program with
  `hiraku program show <program-id>` and follow both that program and the user's
  constraints, permissions, budgets, checkpoints, and stopping condition.

Infer routine identifiers, summaries, hypotheses, metrics, and entrypoint
arguments from the user's request and the repository. Ask a question only when
the missing choice would materially change the research outcome or authorized
scope. Never treat a broad research goal as permission for unrelated external
actions, destructive Git operations, or unlimited resource use.

## Ground every action

Start by discovering the project and current selections:

```bash
hiraku status --json
```

Then inspect only the state relevant to the request:

```bash
hiraku task list --json
hiraku task show [task-id] --json
hiraku experiment list --task <task-id> --json
hiraku experiment show <experiment-id> --task <task-id> --json
hiraku run list --task <task-id> --json
```

Also inspect the repository files needed to understand available code,
entrypoints, metrics, data, and project conventions. Resume existing relevant
work instead of duplicating it. If command syntax or supported options are
uncertain, run `hiraku <command> --help`; do not guess flags.

Tasks and experiments are Markdown records with YAML front matter. Preserve
existing fields and prose when updating them. Core fields are validated;
additional fields such as `hypothesis`, `result`, `conclusion`, `decision`, and
`status` are user metadata rather than an enforced state machine.

## Create and maintain tasks

A task is a research scope, goal, and evolving account of findings, stored in
`hiraku/tasks/{task_id}/task.md`.

For a high-level task-creation request:

1. Check for an existing task that already represents the request.
2. Choose a concise slug when the user did not supply an ID.
3. Translate the request into a concrete goal, research question, and declared
   metrics with their optimization directions when these are known.
4. Create the task and verify the canonical record.

```bash
hiraku task create <task-id> \
  --goal "..." \
  --question "..." \
  --set 'metrics=[{name: val/loss, direction: minimize}]'
hiraku task show <task-id> --json
```

Creating a task activates it by default. Use `--no-set` only when the task
should not become active. Hiraku records a suggested task branch by default;
it does not switch to it. Supply `--branch` only when the user or project
workflow specifies one.

Use `hiraku task update` for requested changes or evidence-backed summaries.
Do not summarize experiments that have not been run or conclusions that the
available runs do not support. For substantial changes to `task.md`, inspect
and preserve the existing Markdown, prepare the revised body, and apply it with
`--markdown-file` rather than overwriting useful context.

## Create and maintain experiments

An experiment is a focused investigation within a task, stored in
`hiraku/tasks/{task_id}/experiments/{experiment_id}.md`. It may be a baseline,
hypothesis, diagnostic, ablation, or other investigation. An experiment without
a source commit is a proposal; binding a commit links it to the code being
evaluated.

For high-level experiment work:

1. Resolve the owning task from the request or active task and inspect it.
2. Check that the experiment ID is not already in use.
3. Capture a short summary and a falsifiable hypothesis when the request
   provides enough information. Brainstorming may produce one or more unbound
   proposals without changing source code.
4. When asked to implement an existing proposal, inspect its hypothesis and the
   relevant code, make and verify the focused change, commit it if authorized,
   then bind that commit with `hiraku experiment update <experiment-id> --commit
   HEAD`.
5. Inspect the runnable code and existing task entrypoints. Declare an
   experiment entrypoint only when an existing one does not already describe
   the requested invocation.
6. Bind `--commit HEAD` during creation only when the experiment already
   represents the current source state. Leave a future idea as an unbound
   proposal and update its commit after implementation.
7. Verify the resulting record, resolved commit, and entrypoint.

```bash
hiraku experiment create <experiment-id> --task <task-id> \
  --summary "..." \
  --hypothesis "..." \
  --commit HEAD \
  --set 'entrypoints={train: {command: [uv, run, python, train.py], args: {seed: 42}}}'
hiraku experiment show <experiment-id> --task <task-id> --json
hiraku experiment entrypoints <experiment-id> --task <task-id> --json
```

Creating an experiment activates it by default. Several experiments may share a
commit and differ by entrypoint arguments. Create or switch Git branches only
when the user or selected program asks for that workflow. Ordinary Git
operations remain responsible for commits and merges.

Use repeatable `--set 'key=<YAML value>'` for custom metadata and `--unset key`
to remove it. `--markdown-file path` replaces the Markdown body, so retain any
notes that are still relevant. Never rebind a completed experiment to make a
different source state appear to be the same attempt.

## Run experiments and inspect evidence

A run is an execution trace owned by CSV, MLflow, W&B, or a custom tracking
backend. Hiraku launches entrypoints and queries runs; the training or evaluation
code writes the run to its backend.

For a high-level run request:

1. Resolve the exact task, experiment, and entrypoint. Inspect inherited and
   experiment-specific entrypoints with `hiraku experiment entrypoints`.
2. Confirm that the experiment is bound to the intended commit and that the
   required code, data, dependencies, and credentials are available.
3. Use `--dry-run` when it materially helps validate a nontrivial dispatch or
   argument override.
4. Launch with an experiment-qualified selector so attribution is explicit.
5. Query that experiment's runs immediately afterward and report the observed
   result. A successful process without a tracked row is not a tracked run.

```bash
hiraku launch <experiment-id>:<entrypoint>
hiraku run list --task <task-id> --experiment <experiment-id> --json
```

Everything after `--` overrides entrypoint arguments:

```bash
hiraku launch <experiment-id>:train -- --seed 7
```

Do not repeat an existing successful run unless the user or workflow requires a
replicate. On failure, preserve the evidence, explain whether the launch failed
or simply produced no tracked row, and stop or continue according to the user's
requested scope. Record conclusions or decisions only when requested or when
they are part of the bounded/autonomous workflow, and cite actual run IDs,
metrics, and source commits.

## Compare runs and carry out research workflows

Use task metrics and compatible runs when comparing experiments. Keep data
splits, budgets, and evaluation definitions comparable, and do not infer
experiment attribution when it is absent.

```bash
hiraku run list --task <task-id> \
  --params seed,epochs \
  --metrics val/log_loss,val/accuracy \
  --sort val/log_loss --ascending --json
```

For a bounded research workflow, repeat the relevant cycle until the user's
requested outcome is reached: inspect prior evidence, formulate one focused
experiment, make only the necessary code change, bind the resulting source
commit when appropriate, launch it, inspect its attributed run, and record an
evidence-backed result. Respect any requested maximum experiments, runs, time,
cost, or hardware budget.

For a named autonomous program, inspect the task, program, experiment history,
runs, Git state, and required files before entering its loop. Resume incomplete
work safely and follow the program's own checkpoints and stopping rules. User
instructions override program defaults.

## Entrypoints, dispatch, and tracking details

Entrypoints are command argument lists, not shell expressions:

```yaml
entrypoints:
  train:
    command: [uv, run, python, train.py]
    args: {seed: 42, epochs: 40}
```

A task entrypoint provides task-only attribution. An
`<experiment-id>:<entrypoint>` selector first checks the experiment, inherits
the task definition when absent, and always attributes the run to that
experiment. Active experiment state never affects launch selection.

`--dispatch NAME` selects `hiraku/dispatch/NAME.sh`; the configured default is
`launch.sh`. The default dispatcher reports the current commit and clean/dirty
state. The `worktree` dispatcher runs the requested commit in a temporary
worktree. Hiraku injects `HIRAKU_TASK_ID`, `HIRAKU_EXPERIMENT_ID`, `HIRAKU_ROOT`,
`HIRAKU_ENTRYPOINT`, record paths, `HIRAKU_COMMIT`, and `HIRAKU_GIT_STATE`.

The default CSV backend uses project-root `runs.csv`. MLflow and W&B require the
training code and Hiraku adapter to use the same configured backend destination.
Hiraku exposes normalized summaries; full curves and artifacts remain in the
tracking backend.

## Finish clearly

Verify the requested end state with `show`, `list`, or `run list --json`. Report
what was created, changed, or run; include task and experiment IDs, entrypoint,
commit, run ID, and key metrics when applicable. Mention failures or missing
attribution explicitly. Stop when the user's requested scope is complete.

The Python API exposes `HirakuConfig` in `hiraku.config`; `Task`, `Experiment`,
`Entrypoint`, `Metric`, `Run`, loaders, and listing functions in `hiraku.data`;
and `hiraku.launch.launch` for scripted use. `hiraku context`, `hiraku action`,
and `hiraku program` manage supporting material and instructions. V1 does not
provide a `hiraku-agent` runner; the current coding agent executes the skill.
