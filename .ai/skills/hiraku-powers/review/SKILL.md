---
name: hiraku-powers-review
description: Use only when dispatched as the hiraku-powers review subagent for a record whose state says review. The main agent dispatches this stage; it runs it inline only without a subagent tool.
---
# Review the record (subagent)

Judge independently whether the evidence supports the hypothesis. Write only
`review-notes.md`, `result.md`, `decision.md`, and ledger entries. Do **not**
read `implement-notes.md`: treat `ledger.md` and `### Deviations` as the
implementer's claims and verify them against git and the runs. Never run
`save`, `bind`, `finalize`, or a launch. You cannot ask the user; put questions
in `review-notes.md`. Scripts and run filtering: `../hiraku-powers/PROTOCOL.md`.

1. `state <id>` (stage `review`; note `base:` and `bound:`, which are equal
   when the plan changed no code). Read the record with
   `hiraku record show <id> --json` (its `markdown` holds the plan), the
   workspace `ledger.md`, the record's runs, the baseline runs named in the
   plan's Evidence (they may be this record's own runs), and
   `git diff <base>..<bound>`.
2. Check each dimension:
   - **Plan fidelity:** the diff holds only planned changes; deviations are
     logged and harmless.
   - **Attribution:** runs carry this record, the bound commit, the entrypoint,
     and a clean tree; the parameter under test is visible in their params; the
     commit was not rebound. The smoke run counts as evidence when it ran at the
     bound commit with the planned args.
   - **Comparability:** same split, seeds, budget, and protocol as the baseline;
     a baseline run on a dirty tree is a finding to weigh, not to ignore.
   - **Margin:** the effect against the seed or Monte Carlo standard error; a
     deterministic rerun of the same seed is not new evidence.
   - **Support:** the success criterion applied as written; every claim cites
     run IDs, metrics, and the commit.
   - **Waste:** unplanned changes, runs, budget overruns, side effects.
3. Write:
   - `review-notes.md`: findings by severity (blocker, major, minor) with
     evidence, then questions for the user.
   - `result.md`: plain text, no heading, starting with `supports:`,
     `refutes:`, or `inconclusive:` and one sentence with run IDs (8-character
     prefixes are fine), metrics, and commit. It becomes the record's `result` field.
   - `decision.md`: plain text, no heading: what to keep, and the next record
     worth defining. It becomes the record's `decision` field.
4. `ledger <id> --marker review-drafted`.

A fix for a finding is a new related record, never an edit here. If the file
tool refuses a file, write it with a shell heredoc.
