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
change code yet. Present the draft to the user, then call `workflow_resume`
with the workflow session ID, checkpoint ID, and proposed response. For
approval interrupts, `workflow_resume` opens a structured MCP elicitation and
waits for the user's choice before resuming. For an approved draft, the proposed
response is `{"event": "drafted", "arguments":
{"record_id": "...", "summary": "...", "hypothesis": "...", "change":
"..."}}`.
If the elicitation is cancelled, explain that approval was not given and leave
the workflow paused; do not resume it or proceed with the experiment.

## Implement

Implement the drafted experiment in the data and commit the change. Show
the user the change, then call `workflow_resume` with the proposed response.

## Launch

Call `workflow_resume` with `{"event": "launched"}` as the proposed response.
The tool elicits approval before resuming. Run the explicit argv supplied when
starting the workflow with `hiraku run --record <experiment> -- <command...>` only after
approval, then resume with the result once the run is tracked; check with
`hiraku runs --record <experiment>`.

## Analyze

Compare the runs in the data with the task's metrics, earlier results, and
the experiment's hypothesis. Present your verdict and conclusion to the user,
then call `workflow_resume` with the proposed analysis response. The tool
elicits approval before resuming.

## Done

Summarize the run for the user: each experiment in the data's
`results` with its verdict, and what you would try next.
