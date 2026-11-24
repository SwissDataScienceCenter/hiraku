---
id: research
description: Run a research task end to end over an experiment tree.
---
# Research

## Setup

Prepare the project so that experiments can run here; this comes before any
research step and is done once. Fix every problem in `problems`:

- Leaderboard. Runs are read from a leaderboard, which names the tracking
  source (CSV, MLflow, or W&B). Create one with `hiraku leaderboard create`
  if `leaderboards` is empty; with several, Hiraku cannot choose one for you.
- Jobs. Commands run locally by default. On a cluster, use the site's existing
  job scripts to determine the resources, and submit with `job_submit` or
  `hiraku job submit` (executors such as Slurm or Renku, `--launcher` to select
  a PSI/J launcher).
- Context. Create a `project-setup` context record with `record_create` or
  `hiraku record create project-setup --type context --markdown-file <file>`.
  Describe the data and model paths, software environment, command execution,
  log locations, and how the leaderboard's tracking source records run IDs,
  record IDs, commits, and metrics. `hiraku run` provides `HIRAKU_RECORD_ID`
  and `HIRAKU_JOB_ID` to the command, but not the commit: the command must
  record the commit it runs at in the tracking source (`hiraku.commit` in a W&B
  run's config or an MLflow run's tags, the `commit` column of a CSV). Hiraku
  only counts runs whose commit is the experiment's, so a run without it never
  reaches the analysis.

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
`failure` is set, an earlier attempt failed to run: fix that first. Then run a smoke
test that exercises the change quickly, as the `project-setup` context
describes (for example a few steps on a tiny input), and fix what it shows.
Commit the change and send `implemented` with the commit, the smoke test's
command, and its output (`smoke_test_required` says whether they are
mandatory).

## Launch

Check out the experiment's commit, then run `command` from the data. The
command runs in the foreground with `hiraku run --record <id> -- <command>`,
or in the background with `hiraku job submit --record <id> -- <command>` (the
MCP `job_submit` tool is equivalent). Send `launched` once execution or submission succeeds. If the
launch fails and the code has to change, send `failed` with the experiment's
record ID and the error.

## Wait

The experiments in the data are launched and have no run with metrics yet.
Check their jobs and runs, for example with `hiraku job status <job-id>` and
`hiraku leaderboard runs --record <id>`. Send
`checked` when a run has finished; Hiraku answers not ready until a run has
metrics. `runs_left_out` lists runs of an experiment that have metrics but not
its commit. A run there without a `commit` means the command did not record the
commit: fix that (it is the code of the experiment) and send `failed` with the
experiment's record ID and the reason. A run with another `commit` comes from an
earlier commit of the experiment, for example before a review fix; it is
ignored, so keep waiting for the run of the current commit. If a job failed
without recording a run, read its log (`hiraku job logs <job-id>`) and send
`failed` with the experiment's record ID and the reason.

## Analyze

Compare the runs in the data with the experiment's hypothesis and expected
outcome, the task's metrics and their direction, and the parent's result if
there is one. Runs with another commit are left out. Send
`analyzed` with a verdict (`supports`, `refutes`, or `inconclusive`) and a
conclusion of a few sentences that cites run IDs and metric values.

## Review design

Review the experiments in the data as an independent expert, before any of
them is implemented. Judge only what is in the data, the task, and the
literature: whether each hypothesis is plausible and worth testing, whether the
planned change actually tests it (a fair comparison, controls, no leakage of
evaluation data), whether the expected outcome can be measured with the task's
metrics, and whether the batch repeats work already in the tree. Use web search
for related work and known results, and your own knowledge; list what you used
in `sources`. Send one verdict for each experiment in `verdicts`, so that a
sound experiment is not held back by a flawed sibling.

Work independently of whoever did the step: rely only on this instruction,
the records, the repository, and your sources, not on earlier conversation. In
a conversation that has done other steps of this study, hand this instruction
to a fresh subagent (for example Claude Code's Agent tool) and send the
response with its findings unchanged. Do not change code, commits, or records
during a review. Send your own session or agent ID in `agent`: one ID cannot act
in two roles of a study.

A decision is `approve` or `fix`. Describe each problem as a finding: `claim`,
`evidence` (run IDs with values, a diff line, a source), `severity`
(`blocking` sends the experiment back; `minor` is recorded as a limitation),
and `affects` (`analysis`, `runs`, `code`, `design`, `setup`, `task`, or
`unknown`). Use `task` or `setup` when the problem is in the task record or the
`project-setup` context: no fix to the experiment helps, and the researcher
decides. Set `unfixable` when no fix to this experiment can resolve a finding
that stays within its scope; the experiment then ends with its review failed:
one that ran keeps its result as provisional, one that never ran is
inconclusive.
`thread` holds the earlier findings and the author's answers. When a finding
stays open after the author disputed it, send it again with `continues` set to
its ID; a meta review then rules on it.

## Review code

Review the experiment's code change as an independent reviewer. Read the
change with the `diff` command and the planned change in the data, and check
the logic: bugs, a change that does not implement the plan, evaluation or data
handling that could bias the result, and entrypoint or run recording that
breaks the `project-setup` context. The author ran a smoke test; read its
command and output in `smoke_test`, and run it again only if the output is
missing or does not show the change working.

Work independently of whoever did the step: rely only on this instruction,
the records, the repository, and your sources, not on earlier conversation. In
a conversation that has done other steps of this study, hand this instruction
to a fresh subagent (for example Claude Code's Agent tool) and send the
response with its findings unchanged. Do not change code, commits, or records
during a review. Send your own session or agent ID in `agent`: one ID cannot act
in two roles of a study.

A decision is `approve` or `fix`. Describe each problem as a finding: `claim`,
`evidence` (run IDs with values, a diff line, a source), `severity`
(`blocking` sends the experiment back; `minor` is recorded as a limitation),
and `affects` (`analysis`, `runs`, `code`, `design`, `setup`, `task`, or
`unknown`). Use `task` or `setup` when the problem is in the task record or the
`project-setup` context: no fix to the experiment helps, and the researcher
decides. Set `unfixable` when no fix to this experiment can resolve a finding
that stays within its scope; the experiment then ends with its review failed:
one that ran keeps its result as provisional, one that never ran is
inconclusive.
`thread` holds the earlier findings and the author's answers. When a finding
stays open after the author disputed it, send it again with `continues` set to
its ID; a meta review then rules on it.

## Review result

Review the experiment's result as an independent reviewer. First, before you
read `analysis`, form your own verdict from the runs, the hypothesis and
expected outcome, the task's metrics and their direction, and the parent's
result; check the numbers against what the literature or your own knowledge
says is plausible for this setup, and list what you used in `sources`. Then
read `analysis` and compare. `runs` are the runs the analysis used, with every
field the tracking source holds; `excluded_runs` are the ones left out, with the
reason: check that nothing was dropped to suit the conclusion. Send the run
values your findings rest on in `evidence` as `{run_id, metric, value}`; Hiraku
checks each against the data. Send your own verdict in `own_verdict`.

Work independently of whoever did the step: rely only on this instruction,
the records, the repository, and your sources, not on earlier conversation. In
a conversation that has done other steps of this study, hand this instruction
to a fresh subagent (for example Claude Code's Agent tool) and send the
response with its findings unchanged. Do not change code, commits, or records
during a review. Send your own session or agent ID in `agent`: one ID cannot act
in two roles of a study.

A decision is `approve` or `fix`. Describe each problem as a finding: `claim`,
`evidence` (run IDs with values, a diff line, a source), `severity`
(`blocking` sends the experiment back; `minor` is recorded as a limitation),
and `affects` (`analysis`, `runs`, `code`, `design`, `setup`, `task`, or
`unknown`). Use `task` or `setup` when the problem is in the task record or the
`project-setup` context: no fix to the experiment helps, and the researcher
decides. Set `unfixable` when no fix to this experiment can resolve a finding
that stays within its scope; the experiment then ends with its review failed:
one that ran keeps its result as provisional, one that never ran is
inconclusive.
`thread` holds the earlier findings and the author's answers. When a finding
stays open after the author disputed it, send it again with `continues` set to
its ID; a meta review then rules on it.

## Fix

Fix the findings of a review, as the author. `kind` says what was reviewed
(`design`, `code`, `result`, or `acceptance`), `findings` are the blocking
findings with their IDs, and `thread` holds everything said so far. Answer every
finding exactly once in `responses`: `done` with what changed, `dispute` with
your argument and evidence, or `limitation` when you accept it and want it
recorded. Hiraku reads what changed from the records and the runs, not from
your answer, and rejects `done` when nothing changed.

- Design: send the amended experiment in `plan`; nothing has run, so the
  hypothesis may change.
- Code: commit the change and send `commit` with the smoke test's command and
  output.
- Result and acceptance: a new commit starts the experiment again from code
  review; new runs for the same commit, or `excluded_runs` (each with a reason),
  start the analysis again; `verdict` and `conclusion` revise the analysis. Wait
  until new runs have metrics before you answer. The hypothesis, the expected
  outcome and the change are frozen once runs exist: a flaw in the idea is a new
  experiment, not an edit.

Stay inside this experiment. Do not edit the task record, the `project-setup`
context, or other experiments; if you do, the researcher is asked.

## Meta review

The author and the reviewer of a stage did not converge on `open_findings`.
Rule on them as an independent meta reviewer, neither the author nor that
stage's reviewer. You see the whole study: the task, `project_setup`, the
`tree`, the experiment, and the full `thread`, plus what the stage's reviewer
saw (the runs, or the diff). Form your own view of each finding from that
evidence before you read the arguments. Rule once on each open finding:
`overrule` (the finding is dropped and the author's position stands),
`limitation` (valid, but it does not block; it is written into the conclusion),
or `sustain` (valid and unresolved: the experiment ends with its review failed,
its result kept as provisional if it ran, or inconclusive if it never ran).
The ruling is final and is recorded. Give a reason for each, list your
`sources`, send the run values you rely on in `evidence`, and send your own
session or agent ID in `agent`.

## Researcher

A problem is outside what one experiment can fix: `question` names the finding
or the records that changed. Ask the researcher and wait for their answer; do
not answer for them. Send `resolved` when they changed the task or the setup (the
stage's reviewer then reviews again), `accept_risk` to continue with the finding
recorded as a limitation, or `conclude` to end this experiment with its review
failed (a result it already has is kept as provisional).
Other experiments keep running; only this one waits.

## Summarize

Write the study report from the tree and the runs: each experiment with its
parent, verdict, and conclusion, the best result on the task's metrics, the
limitations, every experiment with a meta-review ruling (`disputed`), every
result that did not pass review (`provisional`, with the reason), and the open
questions. Report a provisional result as provisional wherever it appears. Every
number must come from a run. If `acceptance_feedback` is set, an earlier report
was reopened: address it. Send the text in `report`.

## Acceptance review

Review the finished study as an independent reviewer who has seen none of its
discussions. Check every number in `report` against the raw runs in `evidence`,
and look for problems that no review of a single experiment can see: the best of
many experiments reported alone, multiple comparisons, a conclusion that the
tree does not support. Check that inconclusive experiments, limitations, every
`disputed` ruling, and every `provisional` result are disclosed, that no
provisional result is reported as established, and that the commit and `command`
allow a rerun. Send `accept`, `accept_with_caveats` with the `caveats`, or
`reopen` with the experiments to reopen and their findings (only when
`reopen_allowed`). Send the run values you rely on in `evidence` and your
session or agent ID in `agent`.

## Triage

Decide what follows from the experiment's verdict. `follow-up` opens new
experiments as its children, designed in the next step; `stop` closes this
branch. Follow up only when the result raises a question worth testing;
`can_follow_up` says whether the depth and experiment limits still allow it.
Send `triaged` with the decision and a one-sentence reason. An experiment that
ran can be followed up even when its review failed: its verdict is then
provisional (`review_failed` says why), so say in the reason how the follow-up
depends on it. One that never ran cannot be followed up.

## Done

Summarize the study for the user from the tree in the data: each experiment,
its parent, verdict, and conclusion; the best result on the task's metrics;
and the open questions.