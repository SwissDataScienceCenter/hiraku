---
name: hiraku
description: Manage Hiraku tasks, experiments, reproducible launches, run analysis, and guided or bounded-autonomous research workflows.
---

# Hiraku

Use Hiraku when working on a repository initialized with `.hiraku.yml`.

1. Resolve the task explicitly or from its `hiraku/task/<task-id>` branch.
2. Read the task's `task.md` and `program.md` before implementation work.
3. Follow every approval boundary and stopping condition in `program.md`.
4. Keep experiment intent and decisions in `tasks/<task-id>/experiments/`.
5. Use `hiraku ... --json` when consuming structured command output.
6. Bind reproducible experiments with `hiraku experiment set <task>:<experiment> --commit <ref>`.
7. Launch only an invocation declared by the experiment record.
8. Summarize reviewed findings in `task.md`; do not treat hidden conversation state as a research artifact.

Before implementation work, verify the current branch matches the task's `branch`. Switch only when directed by the user, except that an automatic switch is permitted when the working tree is clean and it is clearly required by the user's instructions. Guided programs require explicit approval before commits and launches. Auto programs permit actions only within their approved boundaries.
