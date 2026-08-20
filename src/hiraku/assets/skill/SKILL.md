---
name: hiraku
description: Run guided or bounded-auto Hiraku experiment workflows using experiment records, Git branches, named entrypoints, and tracked run tables.
---

# Hiraku experiment workflow

Use this skill when creating, implementing, launching, analyzing, or iterating on a Hiraku
experiment. Discover the project through `.hiraku.yml`, inspect the target `experiment.md` and
`policy.md`, and use the deterministic `hiraku` CLI for lifecycle operations.

Load only the focused workflow required for the current request:

- Creating or refining an experiment: `references/create.md`
- Implementing an approved plan: `references/implement.md`
- Launching or analyzing tracked runs: `references/launch-analyze.md`
- Running a bounded autonomous loop: `references/auto-loop.md`

The experiment mode controls agent behavior, not security enforcement. Never treat a policy file
as proof that a human approved it in the active conversation.

