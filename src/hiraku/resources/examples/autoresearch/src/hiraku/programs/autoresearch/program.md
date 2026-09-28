---
id: autoresearch
summary: Bounded autonomous experimentation with reviewable records and runs.
---
# Autoresearch program

Before starting, inspect `hiraku status --json`, `hiraku record tree`, the active
record, and `hiraku runs --record <root-id> --descendants --json`. Agree with
the user on the objective, time budget, branch, and stopping condition.

Create one root record for the research effort with the objective, metrics,
and shared entrypoints. Create a child record for each attempted code change.
Each child describes one idea, binds the full implementation commit with
`--commit HEAD`, and records the observed result and decision. Keep `commit`,
`status`, and `decision` local to each child. Never rebind a completed attempt
to a different commit.

For each attempt:

1. Inspect prior records and runs; choose one testable idea.
2. Change and commit the implementation on the agreed branch.
3. Create a child record with `hiraku record create <id> --parent <root-id>
   --type experiment --commit HEAD`.
4. Launch with `hiraku launch train --record <id>` and inspect
   `hiraku runs --record <id> --json`.
5. Record the result, failures, and evidence in the child record. Set a local
   decision such as `accepted` or `discarded`.
6. Continue until the agreed stopping condition; summarize the evidence in
   the root record without claiming unobserved results.

The project dispatcher enforces the run timeout and logs status and metrics to
`runs.csv`. Treat missing or malformed output as a failed run. Preserve the
implementation commit and the run log for diagnosis.
