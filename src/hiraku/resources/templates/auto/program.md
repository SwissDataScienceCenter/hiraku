# Bounded autonomous research program

Before starting, obtain human approval and record every boundary below. Do not begin while any
required boundary is blank.

## Approved boundaries

- Goal and scope:
- Fixed evaluation protocol and ordered metric directions:
- Permitted commands and editable files:
- Resource and time limits:
- Permissions, including commit, branch, and integration authority:
- Iteration limit and other stopping conditions:

Within those bounds, repeat this loop:

1. inspect `task.md`, `program.md`, experiments, entrypoints, and `hiraku runs <task> --json`;
2. choose one in-scope hypothesis and create its experiment record and permitted branch;
3. implement one coherent change, commit it, and bind the exact commit;
4. launch only the experiment's declared invocation;
5. diagnose crashes or provenance mismatches, then compare task-declared metrics in their declared
   directions;
6. record status, decision, and conclusion; integrate or reject with normal Git as authorized; and
7. stop if a stopping condition is met, otherwise select the next experiment.

Pause when a boundary is missing, contradictory, or would be exceeded; when scope, evaluation,
commands, dependencies, resources, permissions, tracking backend, or stopping conditions would
change; or when safe progress requires an unapproved external mutation. Failed candidates may be
rejected, while implementation failures should be corrected or recorded separately.

At completion, synthesize reviewed evidence, decisions, open questions, and next steps in `task.md`
for human review. Never edit tracking data, approve or merge a remote review, implicitly push, or
merge into a protected branch without authorization. Research logic is identical for table, local
SQLite MLflow, and remote MLflow. Treat this program as workflow guidance, not a security boundary.
