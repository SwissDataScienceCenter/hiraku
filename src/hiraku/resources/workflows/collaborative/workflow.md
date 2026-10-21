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
experiments first, and build on the previous results in the data. Do not
change code yet. Present the draft to the user. If they want changes, send the
event `revise_draft` with their feedback; the draft instruction comes back
with it.
Once they approve, send the event `drafted` with a short record ID, a one-line
summary, the hypothesis, and the change you plan to make.

## Implement

Implement the drafted experiment in the data and commit the change. Show
the user the change. If they want changes, send the event
`revise_implementation` with their feedback. Once they approve, send the event
`implemented` with the commit.

## Launch

Ask the user before launching; if they want it launched differently, send the
event `revise_launch` with their feedback. Run the launch command from the
context once they agree, and send the event `launched` once the run is tracked;
check with `hiraku runs --record <experiment>`.

## Analyze

Compare the runs in the data with the task's metrics, earlier results, and
the experiment's hypothesis. Present your verdict and conclusion to the user.
If they want changes, send the event `revise_analysis` with their feedback.
Once they approve, send the event `analyzed` with the verdict and a conclusion
of a few sentences.

## Done

Summarize the session for the user: each experiment in the data's
`results` with its verdict, and what you would try next.
