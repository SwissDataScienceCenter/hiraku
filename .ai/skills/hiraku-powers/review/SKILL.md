---
name: hiraku-powers-review
description: Use only when you were dispatched as the hiraku-powers review subagent for a record whose state says review. The main agent dispatches this stage through hiraku-powers/DISPATCH.md and does not run it inline unless it has no subagent tool.
---
# Review the record (subagent brief)

## Subagent brief

You are the review subagent. You are read-only except for `findings.md`,
`result.md`, and `decision.md` in the workspace, and the ledger. You get the
record ID and the `base` and bound commit; derive the run IDs yourself with
`hiraku runs --record <id> --json` (smoke runs carry no tag; the ledger notes
name them). You must **not read** the
implement `report.md`. Treat `ledger.md` and `### Deviations` as the
implementer's self-report and verify claims against `git diff` and
`hiraku runs`, so your judgment stays independent. Never run `push`,
`finalize`, `bind`, or a launch. You cannot ask the user: put questions in
`findings.md`. The main agent presents your drafts and runs `push` and
`finalize` after the user approves. Protocol: `../hiraku-powers/PROTOCOL.md`.

1. `workspace <id>`; read `plan.md`, `ledger.md`, the record, `runs`, and
   `git diff <base>..<commit>`; optionally run the `code-review` skill.
2. Write `findings.md` (by severity), `result.md` (supports, refutes, or
   inconclusive, with run IDs, metrics, and commit), and `decision.md`.
3. `ledger <id> --marker review-drafted` (needs all three drafts non-blank).

### Review dimensions

- **Plan fidelity:** diff contains only planned changes; deviations logged.
- **Attribution:** runs carry the right record, commit, and entrypoint; the
  commit was not rebound.
- **Comparability:** same split, budget, seeds, and protocol as siblings.
- **Margin:** compare the effect against the Monte Carlo or seed standard error;
  a margin inside it is inconclusive.
- **Independence:** a deterministic rerun with the same seed repeats, it is not
  new evidence.
- **Support:** every claim cites run IDs, metrics, and commit; none exceeds the
  evidence; the planned threshold is applied as written. Every metric used is a
  root metric the record produced.
- **Lean check:** waste, unplanned runs, budget overruns, unplanned side effects.
- **Record hygiene:** body preserved, plan ticked, nothing summarized that was
  not run.

## Red flags

| Thought | Reality |
| --- | --- |
| "The metric improved, so supported" | Check comparability, margin, and threshold. |
| "One seed is enough" | Apply the plan's evidence rule; otherwise inconclusive. |
| "Same seed again confirms it" | A deterministic repeat is not independent. |
| "The report says it passed" | Do not read it; verify against the diff and runs. |
| "I'll fix the code in review" | Report it. A fix is a new related record; never rebind. |
