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
experiments first, and build on the previous results in the context. Do not
change code yet. Present the draft to the user and revise it with them until
they approve it; then send the event `drafted` with a short record ID, a
one-line summary, the hypothesis, and the change you plan to make.

## Implement

Implement the drafted experiment in the context and commit the change. Show
the user the change and revise it with them until they approve it; then send
the event `implemented` with the commit and a short note on what changed.

## Launch

Ask the user before launching. Run the launch command from the context once
they agree, and send the event `launched` once the run is tracked; check with
`hiraku runs --record <experiment>`.

## Analyze

Compare the runs in the context with the task's metrics, earlier results, and
the experiment's hypothesis. Present your verdict and conclusion to the user
and revise them with the user until they approve; then send the event
`analyzed` with the verdict and a conclusion of a few sentences.

## Done

Summarize the session for the user: each experiment in the context's
`results` with its verdict, and what you would try next.
