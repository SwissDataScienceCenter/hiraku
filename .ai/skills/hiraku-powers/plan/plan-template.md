# Plan template

Keep the headings; the scripts read `## Plan`, `- Mode:`,
`- Auto-continue to review:`, `- Implement:` (`subagent`, or `inline` in the
small cycle), and `### Deviations`. The hypothesis and success
criterion live in the record fields, not here.

```markdown
## Plan

### Execution
- Branch: <state's branch, or the current branch>
- Entrypoint and args: <name> <args>; set on the record with `hiraku record update --set 'entrypoints=...'` if they differ from the parent
- Dispatcher: <launch | worktree>
- Commit: <one commit, message, or "none: no code change">
- Smoke run: <cheap launch with `--commit HEAD`, or "none: <reason>">
- Full run: <launches after the smoke run, seeds>
- Budget: <max runs, time, hardware>
- Side effects: <expected changed files besides the commit, or none>
- Mode: implement-and-run
- Auto-continue to review: yes
- Implement: subagent

### Tasks
- [ ] 1. <change> - files: <paths> - check: <command and expected result>
- [ ] 2. ...

### Evidence
- Baseline runs: <sibling record and run IDs, or the filter that selects them>
- Comparable on: <split, seeds, budget, protocol>
- Decision rule: <how the runs map to supports, refutes, inconclusive>

### Deviations
```

Tasks are the changes and checks; the smoke run, `bind`, and the full run are
protocol steps the implementer always does, so they are not tasks.
`Mode: implement` stops after the smoke run and `bind`; `implement-and-run`
also does the full run.
