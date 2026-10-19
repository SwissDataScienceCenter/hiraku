# Workflow examples

The `workflow-examples` example installs two small workflows next to the
bundled `collaborative` workflow. Neither does research work or changes files,
so you can use them to try out a new harness, model, or MCP setup before
running a real workflow, and as starting points for your own:

- [`count-steps`](#count-steps), a Python workflow that checks whether a coding
  harness follows Hiraku workflow instructions exactly;
- [`greet`](#greet), a prompt-only workflow whose arguments fill placeholders
  in its `workflow.md`.

## Initialize

In a Git repository, install the example and restart your coding agent so it
loads the `hiraku` MCP server:

```bash
hiraku init --example workflow-examples
```

## Count steps

The workflow is `hiraku/workflows/count-steps/`. Its `## Step` section
contains `{{step}}`, `{{count}}`, and `{{token}}` placeholders, which `next`
fills with `hiraku.markdown.render`, so the agent receives the exact line to
print.

### Run

Invoke the MCP prompt `hiraku:count-steps` (or raw: `/mcp__hiraku__count-steps`) in Claude Code. Note: Codex does not support MCP prompts.

Alternatively, ask your agent (which will invoke the MCP tool) to run the workflow:

> Start the Hiraku count-steps workflow with count 3.

The workflow sends `count` numbered steps (default 5). Each step carries a
fresh random token, and the agent must:

1. print the step line to you, for example `Step 2/3: token 1a2b3c4d`;
2. reply `ack` with that step's token.

After each step the agent tells you how many steps remain and asks whether to
run the next one. It replies `step` only after you agree, or `finish` to stop.
After the last step, or after `finish`, the session completes with a final
instruction: the agent tells you how many steps ran.

### What to look for

- **Every step is printed in order.** A wrong, stale, or invented token is
  rejected, so a skipped or repeated step shows up as a rejected reply.
- **The agent stops after every step** and waits for your answer. Hiraku cannot
  enforce this: an agent that replies `step` without asking fails the check
  even though the workflow accepts the reply.
- **The session completes.** Run `hiraku session list` to see its state, or
  `hiraku session show <session-id>` for the accepted calls.

To test recovery, compact or restart the conversation mid-run and ask the agent
to continue the session; it should resume at the pending step.

### Run from the terminal

`hiraku-agent` runs the same workflow one step at a time with a headless
agent, `claude -p` by default:

```bash
hiraku-agent count-steps --count 3   # start and run step 1
hiraku-agent count-steps step        # run the next step
hiraku-agent count-steps finish      # or stop early
```

Each step starts a new agent process with an empty conversation: it knows only
its prompt, which it gets on standard input. The agent prints the step line,
writes its `ack` reply with the token to a file, and exits; the driver checks
the reply and stops at the next user decision. The prompt and reply of each
step are kept under `hiraku/sessions/<session-id>/<instruction-id>/`, so you
can inspect what the agent saw.

Add `--interactive`, e.g. `hiraku-agent count-steps step --interactive`, to run
the step as a conversation with the agent in your terminal instead: the agent
says what it would do and waits for your go-ahead, and the driver still stops
before the next step.

## Greet

`hiraku/workflows/greet/workflow.md` is the whole workflow; there is no
`workflow.py`:

```markdown
---
id: greet
description: Greet someone a few times; a prompt-only workflow with arguments.
arguments:
  name: {required: true, description: Who to greet}
  times: {type: integer, default: 2, description: How many greetings to print}
  formal: {type: boolean, default: false, description: Whether to greet formally}
---
# Greet

Print {{times}} greetings to {{name}}, one per line. Greet formally:
{{formal}}. Do not change any files.
```

Each `{{name}}` placeholder needs a declared argument, so a typo is an error
when the workflow loads. Start it with the MCP prompt
`/mcp__hiraku__greet name=Ada times=3`, by asking your agent to greet Ada with
the Hiraku greet workflow, or from the terminal:

```bash
hiraku-agent greet --name Ada --formal true
```

Hiraku validates and converts the arguments, renders the body, and sends it as
the session's only instruction. That instruction is final: the session
completes when it starts, so there is nothing to reply, and the agent just
prints the greetings. Copy the directory to turn your own prompt into a
workflow.
