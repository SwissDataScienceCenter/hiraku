---
name: hiraku-workflow
description: Use when the user asks to start, continue, inspect, or complete a Hiraku workflow.
---
# Hiraku workflows

Hiraku workflows are project-defined LangGraph procedures that guide work through
checkpoints. The current coding agent performs the actions; Hiraku stores the
workflow state and enforces its transitions. Follow the user's requested scope,
the workflow instructions, and each interrupt's response schema.

## Before starting

Start or continue a workflow only when the user asks. First inspect the project
and available workflows, then read the selected workflow's instructions and
required inputs:

```bash
hiraku status --json
hiraku workflow list
```

Use `workflow_list` when available through MCP. Inspect relevant records,
existing workflow sessions, runs, source files, and Git state as directed by the
workflow. If work is already underway, continue its existing session instead of
starting a duplicate. Do not assume a workflow's defaults authorize additional
experiments, launches, commits, or external actions.

## Start, inspect, and resume

Start with the selected workflow ID and its declared arguments:

```text
workflow_start(workflow_id, arguments)
```

Keep the returned `workflow_session_id` and `checkpoint_id`. Inspect current
state without advancing it:

```text
workflow_status(workflow_session_id)
```

Resume the same session with the latest checkpoint ID and a response matching
the current interrupt's schema:

```text
workflow_resume(workflow_session_id, response, checkpoint_id)
```

The equivalent CLI commands are `hiraku workflow start`, `status`, and
`resume`. Check `hiraku workflow --help` and the individual command help for
exact CLI options. A stale checkpoint is rejected; fetch the current status and
respond to its current interrupt. Never create a new session just to continue a
paused one.

After every resume, inspect its returned status and interrupts. `status: waiting`
means the graph has paused; it does not necessarily mean the workflow is stuck.
The interrupt may represent another approval, an action to perform, or a request
for a result. `workflow_status` only inspects state; it does not request
approval or advance the graph.

## Approvals and elicitation

Treat the interrupt payload as authoritative. Follow its `actor`,
`interaction.type`, `action`, `step`, `phase`, `instruction`, `data`,
`response_schema`, and `required_capabilities` when present. `next_action` is a
summary; inspect the full interrupt for details. Do not invent response fields
or skip a checkpoint.

For `actor: agent`, complete the requested work and submit its result through
`workflow_resume`, matching `response_schema`. Do not ask the user to perform
agent-owned work. Inspect the returned state and continue while the next action
is agent-owned and within authorized limits. For `actor: human`, present the
concrete proposal or decision and use trusted MCP elicitation. Never put an
approval decision in ordinary tool arguments as a substitute for human
authorization. Approval may be bound to a stored revision. If the user revises,
incorporate feedback and follow the new interrupt. If elicitation is declined
or cancelled, leave the workflow paused. If the client lacks form elicitation,
ask the user to reconnect with a capable client; do not bypass the gate.

Some workflows separate approval from execution. After approval, resume only
with the approved action's result when the graph returns its execution
interrupt. Perform that action exactly within the approved plan, then submit the
result (such as a commit or run ID). The next interrupt may request a distinct
approval, including immediately after a prior approval. Continue the loop until
the workflow completes or the user asks to stop.

If the MCP server reports that the client cannot perform elicitation, do not
bypass the approval gate through a direct response. Explain that the client
needs to reconnect with elicitation support, and leave the workflow paused.

## Collaborative workflow

For the bundled `collaborative` workflow, follow `workflow.md` and these
workflow-specific invariants:

1. **Draft:** Read the task and earlier experiment results. Propose one focused
   experiment through an agent task interrupt, including an exact per-experiment
   command argv. Hiraku stores the draft and then requests human review tied to
   its revision. Incorporate revision feedback into the same draft record. Do
   not implement before approval.
2. **Implement:** Prepare an implementation plan through an agent task
   interrupt. This does not authorize file changes. Hiraku stores the plan and
   returns a separate human approval interrupt tied to the plan revision. After
   approval, implement that plan, show the changes, commit when authorized, and
   submit the commit reference through the implementation result task.
3. **Launch:** Propose the exact command stored on the experiment record through
   an agent task. Hiraku returns a separate launch approval interrupt. Before
   launching or recovering after interruption, inspect runs for that experiment.
   If a run matching the implemented commit and approved command already exists,
   report its ID instead of launching again. Otherwise, after approval, run the
   exact command and submit its tracked run ID through the launch result task.
4. **Analyze:** Use the workflow's run data and hypothesis to prepare an
   evidence-based conclusion through an agent task interrupt. If metrics are
   missing, inspect the tracker and do not invent results. The workflow then
   asks the human to review the result and choose to continue, revise, or stop.
   Continue only when rounds remain and the user agrees.

Do not substitute the workflow-start command for the experiment's saved command
after draft review. Do not claim a run succeeded until it is visible in the
configured tracking backend and attributed to the expected experiment and
commit. Do not infer a result when metrics are missing.

## Completion and failures

Continue until the graph reports completion or reaches the user's stopping
condition. Summarize the workflow session, records or experiments created,
commits, tracked run IDs, important metrics, and conclusions that the evidence
supports. Clearly report any remaining approval, execution, tracking, or
elicitation issue.

If an action fails, preserve existing records, source changes, and run evidence.
Report the observed failure and use the workflow's revise or recovery path when
available. Do not repeat consequential actions until checking whether they
already completed. Do not bypass validation, alter checkpoint history, or
silently change the workflow's scope.
