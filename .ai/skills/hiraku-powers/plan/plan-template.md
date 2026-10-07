# Plan template

The record body is `## Summary` first, then `## Plan`. Keep headings exactly;
later stages parse them.

```markdown
## Summary

- Status: proposed | planned | implemented | run | reviewed
- Hypothesis: <one falsifiable sentence>
- Success criterion: <metric, direction, threshold>
- Approach: <one line: what differs from the baseline>
- Execution: <branch, entrypoint, mode>
- Evidence: <commit, run IDs, key metrics; "none yet">
- Result: <supports | refutes | inconclusive, one line; "pending">
- Next: <the single next action or open question>

## Plan

### Hypothesis and success criterion
One falsifiable sentence. Metric, direction, threshold for support, refutation,
inconclusive.

### Scope
- In: ...
- Out: ...

### Execution
- Branch: <effective branch>
- Dispatcher: launch | worktree
- Entrypoint and args: <name> <args>
- Commit strategy: <one commit, message prefix>. Smoke run with `--commit HEAD`;
  `bind` only after it passes
- Smoke run: <cheap subset or budget, or "none: <reason>">
- Full run: <args, seeds>
- Budget: <max runs, time, cost, hardware>
- Mode: <implement | implement-and-run: exactly one value>
- Auto-continue to review: <yes | no: exactly one value>
- Expected worktree side effects: <e.g. uv.lock re-lock, or none; prefer `uv run --frozen`>

### Tasks
- [ ] 1. <change> - files: <paths> - check: <command and expected result>
- [ ] 2. Instrumentation (if the code does not yet track): metrics written,
  backend, columns, and the root metric each maps to; root metrics this record
  does not produce
- [ ] 3. ...

### Evidence
- Compare against: <sibling ids>
- Comparable on: <split, budget, seeds, protocol>
- Result is read from: <metrics, run filters>

### Deviations
```

Leave `### Deviations` empty and last when the plan is approved: it is the
implementer's log, and the approval pins everything above it.
