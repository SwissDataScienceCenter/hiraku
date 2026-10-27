# Workflow examples

This example installs three small workflows that do no research work:

- `count-steps`, a LangGraph workflow that hands each step to the current
  harness and pauses for the user's decision;
- `greet`, a one-node LangGraph workflow that renders a prompt from `workflow.md`
  and its YAML defaults;
- `scheduler`, a LangGraph workflow that creates experiment records, assigns
  them by priority, and checks each update before assigning the next one.

```bash
hiraku example create workflows .
```

Start count-steps with count 3, greet with `{"name": "Ada", "times": 3}`, or
scheduler through the Hiraku MCP tools. All three examples use the same
LangGraph driver and `workflow_start`, `workflow_status`, and
`workflow_resume` tools. Greet completes immediately after rendering its
prompt.

See `docs/examples/workflows/README.md` in the Hiraku repository for
usage.
