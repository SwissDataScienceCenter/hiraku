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
change code yet. Present the draft to the user, then call the MCP tool
`workflow_approval` with its concise summary and wait for the user's choice.
If they choose stop, resume with `{"event": "finish"}`. If they request changes,
resume with `{"event": "revise_draft", "arguments":
{"feedback": "..."}}`. Once they approve, resume with `{"event": "drafted", "arguments":
{"record_id": "...", "summary": "...", "hypothesis": "...", "change":
"..."}}`.
If the elicitation is cancelled or declined, explain that approval was not given
and leave the workflow paused; do not resume it or proceed with the experiment.

## Implement

Implement the drafted experiment in the data and commit the change. Show
the user the change, then call `workflow_approval` and wait. If they want
to stop, resume with `{"event": "finish"}`. If they want changes, resume with
`{"event":
"revise_implementation", "arguments": {"feedback": "..."}}`. Once they
approve, resume with `{"event": "implemented", "arguments": {"commit":
"..."}}`.

## Launch

Ask the user before launching; if they want it launched differently, send the
event `revise_launch` with their feedback. Once they agree, run the explicit
argv supplied when starting the workflow with `hiraku run --record
<experiment> -- <command...>`. Resume with `{"event": "launched"}` once the
run is tracked; check with `hiraku runs --record <experiment>`.

## Analyze

Compare the runs in the data with the task's metrics, earlier results, and
the experiment's hypothesis. Present your verdict and conclusion to the user,
then call `workflow_approval` and wait. If they want changes, resume with
`{"event": "revise_analysis", "arguments":
{"feedback": "..."}}`. If they choose stop, resume with `{"event": "finish"}`.
Once they approve, resume with `{"event": "analyzed",
"arguments": {"verdict": "supports | refutes | inconclusive", "conclusion":
"..."}}`.

## Done

Summarize the run for the user: each experiment in the data's
`results` with its verdict, and what you would try next.
