---
id: collaborative
description: Draft, implement, launch, and analyze experiments with the user, step by step.
rules: >-
  Use Hiraku's record and run tools, or the `hiraku` CLI (`hiraku record show`,
  `hiraku record list`, `hiraku runs`), to read the task and earlier
  experiments. Never delete records or rewrite Git history.
---
# Collaborative research

LangGraph owns workflow state and transitions. The external coding agent performs
agent-owned tasks, submits each result with `workflow_resume`, and continues
through agent-owned interrupts. Human-owned interrupts require a decision from a
trusted client elicitation; ordinary tool arguments cannot authorize them.

Interrupts include `actor` (`agent` or `human`), `interaction.type` (`task`,
`review`, or `approval`), `action`, `step`, `instruction`, and `data`.
Responses are validated internally with Pydantic; generated JSON Schemas are
not repeated in each checkpointed interrupt. `interaction.mode` is retained
for compatibility. Agent
interrupts are work requests and do not invoke elicitation. Human reviews use
structured elicitation; approvals protect consequential transitions. Approval
payloads bind to a stored revision of the proposal or plan.

For `workflow_start`, `arguments.task` is optional natural-language guidance,
not a record ID. Select the parent task with `context.record_id`; if omitted,
the workflow uses the project's active record. Existing callers may also pass a
record ID in `arguments.record_id` or use `arguments.task` as a record ID when
no active record is selected.

Agent interrupts include `response_fields`. Submit one JSON object with those
fields at the top level.

## Draft and review

Read the task record and earlier experiments. Propose one focused experiment
with a hypothesis, code change, and exact command argv. Submit the result using
the draft response contract. Hiraku stores the proposal as a draft record, then
pauses at a human review gate bound to that draft's revision. The user can
approve, request changes, or stop. On revision, update the same draft record;
do not create a duplicate. Do not implement before the review is approved.

The workflow-start command is only a suggestion. Save the exact per-experiment
command on the draft and use it for launch planning.

## Implementation

Prepare a concrete implementation plan and submit it with the `implement` task
response contract. The workflow stores the plan and returns a separate
human-owned approval interrupt with its revision. MCP elicitation collects
approve, revise (with feedback), or stop. Approval applies only to that plan
revision. After approval, implement that plan, show the user the changes, commit
them when authorized, and submit the commit reference through the
`implement_execution` task interrupt.

## Launch

Prepare the launch proposal using the command saved on the experiment record.
Submit it through the `launch` task response contract. Hiraku stores the exact
command and returns a separate human approval interrupt tied to its revision.
After approval, inspect `hiraku runs --record <experiment>`. If a run for the
implemented commit and approved command already exists, report its run ID
instead of launching again. If the match is uncertain, ask the user before
launching. Otherwise run `hiraku run --record <experiment> -- <command...>`,
inspect the tracked result, and submit its run ID through the
`launch_execution` task interrupt.

## Analyze, review, and iterate

Use the attributed run, metrics, earlier results, and hypothesis to prepare an
evidence-based verdict and conclusion. Submit it through the `analyze` task
response contract. If metrics are missing, inspect the tracker and do not invent a
conclusion. The workflow then asks the human to review the result and choose to
continue, revise the analysis, or stop. A revision returns to agent analysis;
continuing starts another draft only when rounds remain. The final result is
reviewed before the workflow completes.

## Resume and completion

After every `workflow_start` or `workflow_resume`, inspect `next_action` and the
full `interrupts` list. Complete agent-owned tasks and resume with a response
matching the declared response contract; continue while the next action is agent-owned and
within authorized limits. For a human-owned action, present the concrete
proposal and use trusted MCP elicitation. Never simulate human approval. If the
client lacks form elicitation, leave the workflow paused and ask the user to
reconnect with an elicitation-capable client. `workflow_status` inspects state
without eliciting or advancing it.

At completion, summarize experiment records, commits, tracked run IDs, metrics,
conclusions, and remaining limitations. Check for completed side effects before
retrying any interrupted operation.
