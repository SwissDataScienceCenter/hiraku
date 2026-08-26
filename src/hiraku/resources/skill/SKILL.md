---
name: hiraku
description: Manage Hiraku tasks, experiments, reproducible launches, run analysis, and guided or bounded-autonomous research workflows.
---

# Hiraku

Use Hiraku when working on a repository initialized with `.hiraku.yml`.

1. Resolve the task explicitly or from its `hiraku/task/<task-id>` or `hiraku/experiment/<task-id>/<experiment-id>` branch.
2. Read the task's `task.md` and `program.md` before implementation work.
3. Follow every approval boundary and stopping condition in `program.md`.
4. Keep experiment intent and decisions in `tasks/<task-id>/experiments/`.
5. Use `hiraku ... --json` when consuming structured command output.
6. Bind reproducible experiments with `hiraku experiment set <task>:<experiment> --commit <ref>`.
7. Launch only an invocation declared by the experiment record.
8. Summarize reviewed findings in `task.md`; do not treat hidden conversation state as a research artifact.
9. Create a canonical experiment branch with `hiraku exp create <task>:<experiment> --branch [--from <ref>]`; there is no separate branching command.
10. Derive lineage from Hiraku JSON and Git ancestry. Use normal Git, within the program's approval boundary, to integrate accepted work into the task branch.
11. Before `hiraku review create`, synthesize the complete review description in `task.md` and append a concise `## Changelog` entry. Never approve or merge a review.
12. Use `hiraku review create --push` only when publication is explicitly authorized. First-time review creation must run on the task branch; preview and ID-selected updates may run on experiment branches.
13. Inspect runs through Hiraku's backend-neutral JSON. Treat provenance warnings as actionable and use MLflow `--all` only when investigating invalid or unassociated runs.
14. Declare ordered evaluation metrics and `minimize`/`maximize` directions in `task.md`. Use compact `hiraku runs` output for review, `--metrics` to focus columns, `--verbose` for backend metadata, and `--json` for exact provenance.

Before implementation work, verify the current branch matches the task's `branch`. Switch only when directed by the user, except that an automatic switch is permitted when the working tree is clean and it is clearly required by the user's instructions. Guided programs require explicit approval before commits and launches. Auto programs permit actions only within their approved boundaries.
