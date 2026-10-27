# Workflow examples

This example installs three small workflows that do no research work:

- `count-steps`, a LangGraph workflow that hands each step to the current
  harness and pauses for the user's decision;
- `greet`, a Markdown-only workflow whose YAML defaults define typed inputs
  and fill `{{name}}` placeholders in `workflow.md`;
- `scheduler`, a LangGraph workflow that creates experiment records, assigns
  them by priority, and checks each update before assigning the next one.

```bash
hiraku example create workflows .
```

Ask your coding agent to start count-steps with count 3 or scheduler through the
Hiraku MCP tools. Both LangGraph workflows use `workflow_start`,
`workflow_status`, and `workflow_resume`. Greet remains a legacy workflow and
starts with its named MCP tool.

See `docs/examples/workflows/README.md` in the Hiraku repository for
usage.
