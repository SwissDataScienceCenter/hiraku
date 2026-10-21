---
id: research
description: Run a research task end to end over an experiment tree.
---
# Research

## Setup

Prepare the project so that experiments can run here; this comes before any
research step and is done once. Fix every problem in `problems`:

- Dispatcher. On a Slurm cluster (`slurm` is true), set
  `default_dispatch: slurm.sh` in `hiraku.yml` and put this site's `#SBATCH`
  lines into `hiraku/dispatch/slurm.header`. Copy them from a job script that
  already works here (see `job_scripts`), for example the account, partition,
  time, GPUs, memory, and container `--environment`, but not `--job-name` or
  `--output`. Make the dispatcher executable if it is not.
- Context. Create the `project-setup` context with the `context_create` tool or
  `hiraku context create project-setup --markdown-file <file>`. Write down, for
  whoever implements and runs experiments here: where data, models, and caches
  are (absolute paths); the software environment; how a job runs (an
  entrypoint runs the program directly, without `sbatch` or `cd`: the
  dispatcher submits it and runs it in a checkout of the experiment's commit,
  which holds only committed files); where job logs go (for `slurm.sh`,
  `.git/hiraku-worktrees/logs/`); and how a run is recorded: one row in
  `$HIRAKU_ROOT/runs.csv` with a unique `id`, `record_id`, `commit`, and
  `entrypoint` from `HIRAKU_RECORD_ID`, `HIRAKU_COMMIT`, and
  `HIRAKU_ENTRYPOINT`, and a `metric.<name>` column per metric, or the matching
  tags of the configured tracking backend.

Find these facts in the repository (README, CLAUDE.md, job scripts) and ask the
user for what you cannot find. Then send `set_up`; Hiraku checks again.

## Draft

There is no task record yet. Work out the research task with the user: the
goal, the metrics and whether each should be minimized or maximized, and how
one experiment runs. Create the task record with the `record_create` tool or
`hiraku record create`, with `type: task`, `metrics` as a list of
`{name, direction}` (direction `minimize` or `maximize`), and an entrypoint
named as `entrypoint` in the data: `entrypoints: {<name>: {command: [...]}}`.
The entrypoint must follow the `project-setup` context. Then send
`drafted_task` with the task's record ID.

## Design

Design experiments under the parent record in the data. If the parent is the
task, these are the first experiments; if it is an experiment, they follow up
on its verdict and conclusion. Read the task, the parent, and the tree before
proposing, and do not repeat an experiment that is already in the tree.
Propose at most `remaining` experiments. Give each a short new record ID, a
one-line summary, the hypothesis, the outcome that would support it in terms of
the task metrics, and the planned code change. Send them with `designed`;
Hiraku creates the experiment records.

## Implement

Implement the experiment in the data. Start from `parent_commit` when it is
set, otherwise from the task's current code, and keep the change to what the
experiment describes. The entrypoint runs as its command plus `--key value`
options, without a shell, so it must not rely on positional arguments or
placeholders. Each run must be recorded as the `project-setup` context
describes, with the task's metrics. If
`failure` is set, an earlier attempt failed to run: fix that first. Commit the
change and send `implemented` with the commit.

## Launch

Run `command` from the data. Dispatchers such as `slurm.sh` run the
experiment's commit; with `launch.sh`, which runs the current checkout, check
out that commit first. Send `launched`
once the launch is submitted; the run does not need to have finished. If the
launch fails and the code has to change, send `failed` with the experiment's
record ID and the error.

## Wait

The experiments in the data are launched and have no run with metrics yet.
Check their jobs and runs, for example with `hiraku runs --record <id>`. Send
`checked` when a run has finished; Hiraku answers not ready until a run has
metrics. If a job failed without recording a run, read its log and send
`failed` with the experiment's record ID and the reason.

## Analyze

Compare the runs in the data with the experiment's hypothesis and expected
outcome, the task's metrics and their direction, and the parent's result if
there is one. Runs with another commit or entrypoint are left out. Send
`analyzed` with a verdict (`supports`, `refutes`, or `inconclusive`) and a
conclusion of a few sentences that cites run IDs and metric values.

## Triage

Decide what follows from the experiment's verdict. `follow-up` opens new
experiments as its children, designed in the next step; `stop` closes this
branch. Follow up only when the result raises a question worth testing;
`can_follow_up` says whether the depth and experiment limits still allow it.
Send `triaged` with the decision and a one-sentence reason.

## Done

Summarize the study for the user from the tree in the data: each experiment,
its parent, verdict, and conclusion; the best result on the task's metrics;
and the open questions.
