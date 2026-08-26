# Hiraku review workflow

Use this workflow to turn task evidence into the canonical review document and, when authorized, a
GitHub pull request or GitLab merge request.

1. Resolve the task from a selector or current task/experiment branch. Inspect task metadata,
   experiment records, `hiraku runs <task> --json`, lineage, task-branch containment, commit
   mismatches, and existing `pr_id` or `mr_id`.
2. Reconcile contradictions before publication: conclusions without supporting runs, accepted work
   not contained in the task branch, provider/identity mismatches, and unresolved provenance.
3. Synthesize reviewed findings, conclusions, decisions, open questions, and next steps in
   `task.md`. Preserve required front matter and user material that the evidence does not supersede.
4. Append a concise `## Changelog` entry for the review update. `task.md` is sent verbatim as the
   remote description, so preview the exact document and action when the program requires approval.
5. Push only with explicit authorization. Use `hiraku review create` to create or update the
   configured GitHub PR or GitLab MR; use `--push` only when publication is authorized.

First-time review creation runs from the task branch; ID-selected updates may be previewed from an
experiment branch under the CLI restrictions. Never approve or merge a review, and never merge a
protected branch implicitly.
