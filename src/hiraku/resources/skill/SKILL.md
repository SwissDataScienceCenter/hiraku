---
name: hiraku
description: Route Hiraku research and review work in repositories initialized with .hiraku.yml.
---

# Hiraku workflow router

Resolve the task from an explicit selector or the current `hiraku/task/<task-id>` or
`hiraku/experiment/<task-id>/<experiment-id>` branch. Read its `task.md` and `program.md` before
acting; `program.md` is the authority for approvals, permissions, resources, and stopping rules.

- For experiment planning, implementation, binding, launch, run analysis, conclusions, or
  acceptance decisions, read `references/research.md`.
- For task synthesis, changelog maintenance, pushing, or PR/MR creation and updates, read
  `references/review.md`.
- Load both only when the request genuinely spans both lifecycles.

## Shared invariants

- Tasks define goals and evaluation; experiments record hypotheses and decisions; backend-managed
  runs are evidence and must never be edited directly.
- Use Hiraku JSON output for structured consumption. Keep exact commits in records and treat
  commit-mismatch warnings as actionable.
- Launch only the invocation declared by the experiment and bind committed source for reproducible
  runs. Working-tree launches are debugging runs, not reproducible evidence.
- Research logic is identical for table, local SQLite MLflow, and remote MLflow.
- Follow every approval boundary in `program.md`. Never implicitly push, merge, approve a review,
  change task scope or evaluation, or exceed approved autonomous limits.
- Scientific acceptance and Git containment are distinct. Integrate accepted work with normal Git
  only when the program and user authorize it.
