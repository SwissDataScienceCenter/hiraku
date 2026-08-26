# Run a bounded loop

Approve Goal scope, allowed entrypoints, experiment/run limits, time budget, and policy first.

From a skill-aware coding agent, ask it to use the repository-local Hiraku skill and run bounded
autoresearch for the Goal. The skill follows this portable cycle:

```bash
hiraku auto context <goal>
hiraku auto baseline <goal>
hiraku auto proposal create <goal>/<experiment> --hypothesis "..."
hiraku auto next <goal>
# the agent edits source on the checked-out Experiment branch
hiraku auto implement <goal>/<experiment> --summary "..."
hiraku launch <goal>/<experiment>:<approved-entrypoint>
hiraku auto analyze <goal>/<experiment>
```

The agent repeats until a bound or stop condition is reached. Hiraku creates branches, commits agent
changes, launches Runs, computes verdicts, and records events through the shared core.

For unattended use, connect any runtime through an executable adapter:

```bash
hiraku auto loop <goal> \
  --adapter ./my-agent-adapter \
  --max-experiments 5 \
  --max-runs 20 \
  --time-budget 2h
```

The adapter is not an agent-specific command embedded in Hiraku. It is a user-supplied bridge that
implements the versioned JSON stdin/stdout contract in [../agent-protocol.md](../agent-protocol.md).
