# Workflow examples

The `workflows` example installs three small workflows next to the
bundled `collaborative` workflow. None does research work, so you can use them
to try out a new harness, model, or MCP setup before running a real workflow,
and as starting points for your own:

- [`count-steps`](#count-steps), a LangGraph workflow that hands each step to
  the current harness and pauses for the user's decision;
- [`greet`](#greet), a prompt-only workflow whose arguments fill placeholders
  in its `workflow.md`;
- [`scheduler`](#scheduler), a LangGraph workflow that creates and assigns
  records by priority, then checks the harness's reported update.

## Initialize

In a Git repository, install the example and restart your coding agent so it
loads the `hiraku` MCP server:

```bash
hiraku example create workflows .
```

## Count steps

The workflow lives in `hiraku/workflows/count-steps/workflow.py`, with its
instruction text and default count in `workflow.md`. LangGraph owns the loop,
token validation, and checkpointed state. The graph pauses at each step and
returns an instruction for the current coding harness to carry out.

Ask Codex to start count-steps with count 3. It calls `workflow_start` and
receives a `run_id` plus a structured interrupt containing the step text. Show
the line to the user, then wait for their choice. Resume with
`workflow_resume(run_id, {"token": "...", "decision": "continue"})`, or use
`"finish"` to stop. `workflow_status(run_id)` inspects progress without
advancing it. The token must match the current step; invalid responses pause
again with an explanation.

The checkpoint is stored in SQLite, so the run remains resumable after the MCP
server restarts. Both count-steps and scheduler use the LangGraph driver; greet
continues to exercise Hiraku's existing workflow/session path.

## Greet

`hiraku/workflows/greet/workflow.md` is a Markdown-only workflow with no Python
sidecar. Front-matter defaults define its inputs and their types:

```markdown
---
id: greet
description: Greet someone a few times; a prompt-only workflow with arguments.
arguments:
  name: World
  times: 2
  formal: false
---
# Greet

Print {{times}} greetings to {{name}}, one per line. Greet formally:
{{formal}}. Do not change any files.
```

Validated values fill the placeholders. With no overrides, it greets World twice.
Start it with
the MCP prompt
`/mcp__hiraku__greet name=Ada times=3`, by asking your agent to greet Ada with
the Hiraku greet workflow.

Hiraku validates and converts the arguments, renders the body, and sends it as
the session's only instruction. That instruction is final: the session
completes when it starts, so there is no event to send, and the agent just
prints the greetings. Copy the directory to turn your own prompt into a
workflow.

## Scheduler

The workflow is `hiraku/workflows/scheduler/workflow.py`, with its defaults and
instructions in `workflow.md`. Its next assignment comes from project records:

1. `start` creates a task record `scheduler-<id>` and `records` experiment
   records below it (default 3), each with a random `priority` from 1 to 9 and
   `count: 0`.
2. Hiraku assigns the open record with the highest priority, ties going to the
   larger ID. A record is open while its `count` is below `target` (default 2).
3. The harness raises the assigned record's `count` by one, with
   `hiraku record update <record> --set count=<n>` or the `record_update` tool,
   then resumes with `{"record_id": "<assigned-record>"}`.
4. The graph verifies that the response names the assigned record and that its
   count is exactly one higher than before. It then assigns the next record.
5. When every count reaches `target`, the graph returns the final record summary.

Ask your coding agent to start the scheduler with records 3 and target 2. It
uses the same `workflow_start`, `workflow_status`, and `workflow_resume` MCP
tools as count-steps. The graph creates and assigns records; the harness
performs each record update.
