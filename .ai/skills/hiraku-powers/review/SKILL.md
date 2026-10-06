---
name: hiraku-powers-review
description: Use when a Hiraku record's state says review, meaning implementation finished and its result must be checked and drafted; read by the review subagent the main agent dispatches. Stage 4 of hiraku-powers.
---
# Review the record (subagent drafts, main agent presents)

## Subagent brief

You are the review subagent. You are read-only except for `findings.md`,
`result.md`, and `decision.md` in the workspace, and the ledger. You get the
record ID, the `base` and bound commit, and run IDs. You must **not read** the
implement `report.md`. Treat `ledger.md` and `### Deviations` as the
implementer's self-report and verify claims against `git diff` and
`hiraku runs`, so your judgment stays independent. Never run `push`,
`finalize`, `bind`, or a launch. You cannot ask the user: put questions in
`findings.md`. Protocol: `../hiraku-powers/PROTOCOL.md`.

1. `workspace <id>`; read `plan.md`, `ledger.md`, the record, `runs`, and
   `git diff <base>..<commit>`; optionally run the `code-review` skill.
2. Write `findings.md` (by severity), `result.md` (supports, refutes, or
   inconclusive, with run IDs, metrics, and commit), and `decision.md`.
3. `ledger <id> --marker review-drafted`.

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

## Main agent

<HARD-GATE>
Nothing is written to the record's `result`, `decision`, or status until the
user approved the draft. `push` and `finalize` are run by the main agent only.
</HARD-GATE>

1. Dispatch with [reviewer-prompt.md](reviewer-prompt.md) only when `state`
   says `review`, passing `base:` and `bound:` from `state`. Do not pass or
   mention `report.md`.
2. When `state` says `present`, present findings and drafts, plus suggested
   follow-up proposals. Do not re-dispatch; the drafts may already be discussed.
3. On approval update `summary.md` (`Result`, `Evidence`, `Status: reviewed`,
   `Next`), `push <id>`, `finalize <id>`, and verify with `record show`.

Stop at the requested scope. A fix after a blocker is a new related record
through `hiraku-powers-define`; never rebind.

## Red flags

| Thought | Reality |
| --- | --- |
| "The metric improved, so supported" | Check comparability, margin, and threshold. |
| "One seed is enough" | Apply the plan's evidence rule; otherwise inconclusive. |
| "Same seed again confirms it" | A deterministic repeat is not independent. |
| "The report says it passed" | Do not read it; verify against the diff and runs. |
| "I'll fix the code in review" | Report it. A fix is a new related record; never rebind. |
