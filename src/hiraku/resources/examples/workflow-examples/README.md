# Workflow examples

This example installs three small workflows that do no research work:

- `count-steps`, a Python workflow that checks that a coding harness follows
  Hiraku workflow instructions and stops for the user after each step;
- `greet`, a prompt-only workflow: a `workflow.md` whose arguments fill
  `{{name}}` placeholders;
- `scheduler`, which creates experiment records with random priorities and
  lets the agent raise their counts, highest priority first, until each
  reaches a target.

```bash
hiraku init --example workflow-examples
hiraku-agent count-steps --count 3   # run step 1; or start it over MCP
hiraku-agent count-steps step        # run the next step
hiraku-agent greet --name Ada        # one prompt, no events
hiraku-agent scheduler               # work through records by priority
```

See `docs/examples/workflow-examples/README.md` in the Hiraku repository for
usage.
