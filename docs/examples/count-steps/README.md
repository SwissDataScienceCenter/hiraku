# Count steps

The `count-steps` example installs a workflow that checks whether a coding
harness follows Hiraku workflow instructions exactly. It does no research work
and changes no files, so you can use it to try out a new harness, model, or MCP
setup before running a real workflow.

## Initialize

In a Git repository, install the example and restart your coding agent so it
loads the `hiraku` MCP server:

```bash
hiraku init --example count-steps
```

## Run

Invoke the MCP prompt `hiraku:count-steps` (or raw: `/mcp__hiraku__count-steps`) in Claude Code. Note: Codex does not support MCP prompts.

Alternatively, ask your agent (which will invoke the MCP tool) to run the workflow:

> Start the Hiraku count-steps workflow with count 8.

The workflow sends `count` numbered steps (default 10). Each step carries a
fresh random token, and the agent must:

1. print the step line to you, for example `Step 3/8: token 1a2b3c4d`;
2. reply `ack` with that step's token.

Step 5 is a gate. The agent shows you its plan for the remaining steps and asks
whether to continue. It replies `proceed` with the gate's token only after you
agree, or `finish` to stop. After the last step, the session completes with the
number of steps done.

## What to look for

- **Every step is printed in order.** A wrong, stale, or invented token is
  rejected, so a skipped or repeated step shows up as a rejected reply.
- **The agent stops at step 5** and waits for your answer. Hiraku cannot enforce
  this: an agent that replies `proceed` without asking fails the check even
  though the workflow accepts the reply.
- **The session completes.** Run `hiraku session list` to see its state, or
  `hiraku session show <session-id>` for the accepted calls.

To test recovery, compact or restart the conversation mid-run and ask the agent
to continue the session; it should resume at the pending step.
