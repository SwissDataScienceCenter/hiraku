# Bounded autonomous workflow

This workflow is runtime-neutral. Follow it from any capable coding agent; do not assume a vendor,
model, SDK, or agent CLI. Hiraku remains the only writer of research lifecycle state.

Before starting, obtain approval for the Goal scope, allowed entrypoints, maximum Experiments,
maximum Runs, time budget, and policy. Record the limits in your working context and stop when the
first limit is reached.

1. Read `hiraku goal show <goal> --json` and `hiraku auto context <goal>`.
2. Run `hiraku auto baseline <goal>`. This is idempotent and establishes evidence for the immutable
   Goal source.
3. Generate one falsifiable hypothesis grounded in the Goal, recorded Runs, lineage, and previous
   failures. Use a new lowercase Experiment ID.
4. Record it with `hiraku auto proposal create <goal>/<experiment> --hypothesis "..."`. Add
   `--parent <goal>/<parent>` only when refining a parent with a frozen evaluated commit.
5. Run `hiraku auto next <goal>`. Hiraku admits the proposal, creates the ordinary Experiment, and
   checks out its branch.
6. Implement only that hypothesis. Do not edit `.hiraku.yml`, manifests, policies, the evaluation
   protocol, generated reports, or `.hiraku/state.db`.
7. Run `hiraku auto implement <goal>/<experiment> --summary "..."`. Hiraku validates and commits
   the source changes.
8. Launch only an approved entrypoint with `hiraku launch <goal>/<experiment>:<entrypoint>`.
9. Run `hiraku auto analyze <goal>/<experiment>`. Hiraku computes the primary-metric verdict from
   durable Runs and records it.
10. Re-read `hiraku auto context <goal>`, learn from the evidence, and repeat from step 3.

Stop before changing the Goal objective, metric, direction, protocol, constraints, manifest,
budget, policy, permissions, or allowed commands. Stop on an implementation or execution failure
after recording the surfaced error. Missing or invalid primary metrics produce an inconclusive
result, not an invented success.

For unattended integration, an external adapter may run the same cycle through
`hiraku auto loop <goal> --adapter <executable>`. The adapter contract is JSON over stdin/stdout and
is independent of any particular agent runtime.
