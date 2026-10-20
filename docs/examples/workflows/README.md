# Workflow examples

The `workflows` example installs three small workflows next to the
bundled `collaborative` workflow. None does research work, so you can use them
to try out a new harness, model, or MCP setup before running a real workflow,
and as starting points for your own:

- [`count-steps`](#count-steps), a Python workflow that checks whether a coding
  harness follows Hiraku workflow instructions exactly;
- [`greet`](#greet), a prompt-only workflow whose arguments fill placeholders
  in its `workflow.md`;
- [`scheduler`](#scheduler), a Python workflow that picks its next step from
  the project's records, by priority.

## Initialize

Clone the example, open its directory, and restart your coding agent so it
loads the `hiraku` MCP server:

```bash
hiraku clone workflows
cd workflows
```

## Count steps

The workflow is `hiraku/workflows/count-steps/`. Its `## Step` section
contains `{{step}}`, `{{count}}`, and `{{token}}` placeholders, which its
`running` state fills with `hiraku.markdown.render`, so the agent receives the
exact line to print.

### Run

Invoke the MCP prompt `hiraku:count-steps` (or raw: `/mcp__hiraku__count-steps`) in Claude Code. Note: Codex does not support MCP prompts.

Alternatively, ask your agent (which will invoke the MCP tool) to run the workflow:

> Start the Hiraku count-steps workflow with count 3.

The workflow sends `count` numbered steps (default 5). Each step carries a
fresh random token, and the agent must:

1. print the step line to you, for example `Step 2/3: token 1a2b3c4d`;
2. send the event `ack` with that step's token.

After each step the agent tells you how many steps remain and asks whether to
run the next one. It sends `step` only after you agree, or `finish` to stop.
After the last step, or after `finish`, the session completes with a final
instruction: the agent tells you how many steps ran.

Its states and events, from `hiraku workflow graph count-steps`:

```mermaid
stateDiagram-v2
  [*] --> running
  running --> waiting: ack
  running --> finished: ack
  waiting --> running: step
  waiting --> finished: finish
  finished --> [*]
```

The agent acknowledges a running step; at the `waiting` gate you go on or
finish, so the agent cannot end the session on its own.

### What to look for

- **Every step is printed in order.** A wrong, stale, or invented token is
  rejected, so a skipped or repeated step shows up as a rejected event.
- **The agent stops after every step** and waits for your answer. Hiraku cannot
  enforce this: an agent that sends `step` without asking fails the check
  even though the workflow accepts the event.
- **The session completes.** Run `hiraku session list` to see its state, or
  `hiraku session show <session-id>` for the accepted calls.

To test recovery, compact or restart the conversation mid-run and ask the agent
to continue the session; it should resume at the current step.

### Run from the terminal

Use `hiraku-agent count-steps --interactive` for the conversations in which
this workflow asks you whether to continue:

```bash
hiraku-agent count-steps --count 3 --interactive
hiraku-agent count-steps --interactive         # resume after pausing
hiraku-agent count-steps step --interactive    # continue when step is offered
hiraku-agent count-steps finish --interactive  # or stop early
```

Each instruction starts a new agent conversation. The agent prints the step
line, writes its `ack` event with the token to a file, and exits; the driver
checks the event and opens the next conversation, which asks whether to continue.
Approval is expressed in the prompt text, not enforced by the runtime.

Exit an interactive agent without writing an event to pause the session.
Resume the pending instruction with `hiraku-agent count-steps --interactive`,
or send one of its accepted events explicitly. The prompts and events are kept
under `hiraku/sessions/<session-id>/<instruction-id>/` for inspection. Headless
mode is intended for workflows that do not need a user conversation.

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
the Hiraku greet workflow, or from the terminal:

```bash
hiraku-agent greet --name Ada --formal true
```

Hiraku validates and converts the arguments, renders the body, and sends it as
the session's only instruction. That instruction is final: the session
completes when it starts, so there is no event to send, and the agent just
prints the greetings. Copy the directory to turn your own prompt into a
workflow.

## Scheduler

The workflow is `hiraku/workflows/scheduler/`. Its next step does not follow a
fixed sequence; it comes from the records in the project:

1. `start` creates a task record `scheduler-<id>` and `records` experiment
   records below it (default 3), each with a random `priority` from 1 to 9 and
   `count: 0`.
2. Hiraku assigns the open record with the highest priority, ties going to the
   larger ID. A record is open while its `count` is below `target` (default 2).
3. The agent raises the assigned record's `count` by one, with
   `hiraku record update <record> --set count=<n>` or the `record_update`
   tool, and sends the event `incremented` with the record ID.
4. Hiraku checks the evidence: the event must name the assigned record, and its
   count must be exactly one higher than when it was assigned. Then it assigns
   the next record.
5. When every count reaches `target`, a final instruction asks the agent to show
   you the records.

There is no user gate, so the agent works on its own:

```bash
hiraku-agent scheduler --records 3 --target 2   # six steps, one agent each
hiraku record tree                               # the records and their counts
```

Assigning a record is a decision, so `start` and the `incremented` event make
it and store it in the workflow's data; the `working` state only shows the
assignment. An
event can then be checked against what the agent was told, and `session_show`
returns the same assignment until the agent reports it.
