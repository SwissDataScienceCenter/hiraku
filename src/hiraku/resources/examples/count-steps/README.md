# Count-steps example

This example installs the `count-steps` workflow, which checks that a coding
harness follows Hiraku workflow instructions and stops for the user after each
step.

```bash
hiraku init --example count-steps
hiraku-agent count-steps --count 3   # run step 1; or start it over MCP
hiraku-agent count-steps step        # run the next step
```

See `docs/examples/count-steps/README.md` in the Hiraku repository for usage.
