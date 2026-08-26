---
name: hiraku
description: Run guided or bounded-autonomous research using the repository-local Hiraku CLI.
---

# Hiraku

Use `hiraku` as the only writer of Goal, Experiment, Proposal, and Run lifecycle state.
Never edit `.hiraku/state.db` or generated reports.

Route guided work through [guided.md](guided.md) and bounded autonomous work through
[auto.md](auto.md). Before acting, inspect the Goal and Experiment with CLI JSON output.

Core invariant: a Goal has an immutable baseline commit; an Experiment is one implementation
attempt and freezes its exact commit on first launch; Runs repeatedly evaluate that commit.
Later source changes require a new child or sibling Experiment.

