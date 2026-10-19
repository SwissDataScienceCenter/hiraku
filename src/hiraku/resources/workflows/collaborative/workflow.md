---
id: collaborative
description: Draft, implement, launch, and analyze experiments with the user, step by step.
rules: >-
  Use Hiraku's record and run tools, or the `hiraku` CLI (`hiraku record show`,
  `hiraku record list`, `hiraku runs`), to read the task and earlier
  experiments. Never delete records or rewrite Git history.
---
# Collaborative research

## Draft

Draft one experiment for the task. Read the task record and its earlier
experiments first, build on the previous results in the context, and address
the user's `feedback` if there is any. Do not change code yet. Send the event
`drafted` with a short record ID, a one-line summary, the hypothesis, and the
change you plan to make.

## Implement

Implement the drafted experiment in the context, following the user's
`feedback` if there is any. Commit the change and send the event `implemented`
with the commit and a short note on what changed.

## Launch

Run the launch command from the context. Send the event `launched` once the
run is tracked; check with `hiraku runs --record <experiment>`.

## Analyze

Compare the runs in the context with the task's metrics, earlier results, and
the experiment's hypothesis. Send the event `analyzed` with a verdict and a
conclusion of a few sentences.

## Review

Present the result of the step in the context to the user. Ask how to continue
and wait for the answer: the next step, `revise` with the user's feedback to
redo this step, or `finish` to stop.

## Done

Summarize the session for the user: each experiment in the context's
`results` with its verdict, and what you would try next.
