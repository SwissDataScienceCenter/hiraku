# Hiraku research workflow

Use this workflow for one experiment lifecycle.

1. Resolve the task and read `task.md`, `program.md`, relevant experiments, declared entrypoints,
   and current runs. Verify the current branch is appropriate.
2. Confirm the goal, scope, fixed evaluation protocol, ordered metrics and directions, permitted
   commands, resources, permissions, and stopping conditions. Ask only when a missing choice would
   materially alter the experiment or exceed the program.
3. Propose one coherent experiment: ID, summary, falsifiable hypothesis, exact declared entrypoint
   invocation, and branch choice when applicable. Observe guided checkpoints or autonomous bounds.
4. Implement only on the permitted task or experiment branch. Commit the coherent source and bind
   the exact commit with `hiraku exp set <task>:<experiment> --commit <ref>`.
5. Launch only the declared invocation. A dirty or mismatched launch is useful for debugging but is
   not reproducible experiment evidence.
6. Read evidence with `hiraku runs <task> --json`. Never edit `runs.csv` or MLflow data. Diagnose
   crashes and provenance problems before interpreting a scientific result.
7. Compare the task's primary and secondary metrics in their declared minimize/maximize directions.
8. Record experiment status, decision, and a conclusion supported by the runs. Distinguish a failed
   hypothesis from a correctable implementation failure.
9. Integrate accepted work or abandon rejected work only as directed by `program.md`, using normal
   Git. Acceptance does not itself prove the experiment commit is contained in the task branch.

The same lifecycle applies to linear histories, revert/reset programs, and retained experiment
branches. Git ancestry supplies lineage; the task-level reviewed synthesis belongs in `task.md`.
