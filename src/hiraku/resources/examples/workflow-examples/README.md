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
hiraku init --example workflow-examples
hiraku-agent count-steps --count 3 --interactive  # or start it over MCP
hiraku-agent count-steps --interactive            # resume after pausing
hiraku-agent greet --name Ada        # one prompt, no events
hiraku-agent scheduler               # work through records by priority
```

See `docs/examples/workflow-examples/README.md` in the Hiraku repository for
usage.
