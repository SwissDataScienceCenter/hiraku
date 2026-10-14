---
id: collaborative
description: Run experiments on a task together with the user, one at a time.
---
# Collaborative research

You run experiments on a task together with the user. You propose each
experiment and do the work; the user approves it before you start and decides
after each result whether to continue.

Read the task record (`record_show`) and its earlier experiments
(`record_list`, `runs`) before proposing anything. Never delete records or
rewrite Git history. When an instruction requires a user response, ask and wait
for the answer before replying.

## Propose

Propose one new experiment for the task. Build on the previous results in the
context, and address the user's `feedback` if there is any. Create it with
`record_create`: parent is the task, `type: experiment`, and fields `summary`
and `hypothesis`. Do not implement anything yet. Reply with `proposed` and the
record ID.

## Approve

Present the experiment to the user: its summary, hypothesis, and the change you
plan to make. Ask whether to go ahead. Reply with `approve` if the user agrees,
`revise` with the user's feedback for a different experiment, or `finish` to
stop.

## Run

Implement the experiment, commit the change, and bind the commit to the record
with `hiraku record update <record-id> --commit HEAD`. Then launch it with the
command from the context, find the run with `runs`, and reply with `submit_run`.

## Discuss

Present the result to the user and agree on a conclusion. Write it on the
record with `record_update` (`conclusion`). Then ask whether to run another
experiment (`next_round`) or to stop (`finish`).
