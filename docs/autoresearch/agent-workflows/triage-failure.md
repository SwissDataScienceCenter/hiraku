# Skill reference: triage a disappointing result

Loaded when a run has landed and the
result does not meet the node's expectation. Authority comes from the node's `policy.md`; this
reference never widens it.

## Preconditions

Stop and say so if any of these is false:

- The node's `expectation` was recorded **before** its first run. If it was not, this cannot be
  triaged — the expectation may have been fitted to the result. Record that, and re-register the
  expectation before launching again.
- The node's `Evaluation protocol` matches its goal's.
- At least one run row exists for this `experiment_id`.

## 1. Classify

Read the run rows and the code at the recorded commit. Assign exactly one class and state the
evidence for it in **Findings**.

- **A — execution defect.** The run did not test the expectation. Crash, non-zero exit, shape or
  dtype error, split leakage, metric computed on the wrong subset, stale inputs, a silent fallback.
  Check first: does `reproducible` say `false`? A working-tree run's code is not recoverable, so
  class A cannot be ruled out. Re-run from a commit before concluding anything.
- **B — expectation refuted.** Implementation sound, protocol followed, predicted effect absent.
- **C — inconclusive.** Name the competing explanation the evidence cannot rule out. If you cannot
  name one, it is class B. Do not use C as a default.

For class A: fix on the same node, re-run, do not create a child, and do not set a verdict. Record
the defect and which runs it invalidates.

## 2. Apply the investigation test

Only for B and C. All six must hold; answer each in writing.

1. **Named cause** — a specific mechanism, not "tune more" or "try bigger".
2. **Discriminating** — two named explanations and what each predicts.
3. **Novel** — no node in this goal's tree, `abandoned` and `concluded` included, already tested it.
   Read the tree before answering. Re-proposing a settled question is the most common failure here.
4. **In scope** — inside the goal's scope, under its unmodified protocol.
5. **Within budget** — the goal's remaining runs and depth limit in `research/config.md`.
6. **Proportionate** — expected information worth the cost.

Any failure ends the investigation. Conclude the node, name the condition that failed, and write
what would change the answer.

## 3. Conclude the parent

Always, before proposing anything. Set `status: concluded` and `verdict` (`refutes` for B,
`inconclusive` for C), fill **Findings** and **Conclusions** with the run IDs that support them, and
write **Decisions and next steps**. A child must never be created while its parent is still open —
that is how a tree ends up with no recorded findings and a long chain of running nodes.

## 4. Propose the child

`kind: diagnostic` for C, and for B when a named cause is being separated. `parent` is the node just
concluded. `goal` is inherited. `origin: agent-diagnostic`. `mode` and policy inherited, never
widened.

- **Guided node** → write `research/proposals/<id>.md` and present it. Create nothing else. Say
  plainly that no branch, directory, or run exists yet.
- **Auto node** → create the node and its branch within budget. Pause and ask before exceeding any
  bound, changing scope or protocol, adding a dependency or dataset, or changing mode.

## Never

- Set a verdict on a node whose runs were all non-reproducible without saying so explicitly.
- Rewrite an expectation after seeing results.
- Promote to `research/leaderboard.md` from triage. Promotion has its own gate and its own evidence.
- Delete or edit run rows. The table is append-only; a wrong row is corrected by a new row and a
  note in **Findings**.
- Report "needs further investigation" without naming the mechanism and the discriminating run.
