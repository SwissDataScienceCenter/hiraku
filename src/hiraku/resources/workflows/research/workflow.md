---
id: research
description: Run a research task end to end over an experiment tree.
---
# Research

## Setup

Prepare the project so that experiments can run here; this comes before any
research step and is done once. Fix every problem in `problems`:

- Dispatcher. Commands run directly by default. If the project uses a
  dispatcher, configure it in `hiraku.yml` and ensure its script is executable.
  On a cluster, use the site's existing job scripts to determine the resources
  and submission method.
- Context. Create a `project-setup` context record with `record_create` or
  `hiraku record create project-setup --type context --markdown-file <file>`.
  Describe the data and model paths, software environment, command execution,
  log locations, and how the configured tracking backend records run IDs,
  record IDs, commits, and metrics. `hiraku run` provides `HIRAKU_RECORD_ID`
  and `HIRAKU_COMMIT` to the command.

Find these facts in the repository (README, CLAUDE.md, job scripts) and ask the
user for what you cannot find. Then send `set_up`; Hiraku checks again.

## Draft

There is no task record yet. Work out the research task with the user: the
goal, the metrics and whether each should be minimized or maximized, and how
one experiment runs. Create the task record with the `record_create` tool or
`hiraku record create`, with `type: task`, `metrics` as a list of
`{name, direction}` (direction `minimize` or `maximize`). The command in the
data must follow the `project-setup` context. Then send
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
experiment describes. The command is executed as an argument list without a
shell. Each run must be recorded as the `project-setup` context
describes, with the task's metrics. If
`failure` is set, an earlier attempt failed to run: fix that first. Commit the
change and send `implemented` with the commit.

## Launch

Check out the experiment's commit, then run `command` from the data. The
command runs in the foreground unless the configured dispatcher submits it
elsewhere. Send `launched` once execution or submission succeeds. If the
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
there is one. Runs with another commit are left out. Send
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