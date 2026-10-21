# Workflow examples

This example installs three small workflows that do no research work:

- `count-steps`, a Python workflow that checks that a coding harness follows
  Hiraku workflow instructions and stops for the user after each step;
- `greet`, a Markdown-only workflow whose YAML defaults define typed inputs
  and fill `{{name}}` placeholders in `workflow.md`;
- `scheduler`, which creates experiment records with random priorities and
  lets the agent raise their counts, highest priority first, until each
  reaches a target.

```bash
hiraku init --example workflows
```

Ask your coding agent to start count-steps with count 3, greet with name Ada,
or scheduler through the Hiraku MCP tools. Resume with `session_show`.

See `docs/examples/workflows/README.md` in the Hiraku repository for
usage.
