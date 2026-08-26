# Guided research program

Work through one complete research lifecycle with the data scientist. Tracking may use the table,
local SQLite MLflow, or remote MLflow; never edit backend-managed run data.

Pause for explicit approval at each checkpoint:

1. **Task definition:** scope, goal, fixed evaluation protocol, ordered metrics and directions.
2. **Experiment intent:** hypothesis, exact declared invocation, and linear or branch workflow.
3. **Implementation:** proposed source changes and the coherent commit to bind.
4. **Launch:** exact command, bound commit, resources, and expected tracking destination.
5. **Interpretation:** JSON run evidence, provenance, metric comparison, status, decision, and
   experiment conclusion.
6. **Synthesis and publication:** reviewed `task.md`, changelog entry, Git integration, and optional
   PR/MR action.

After approval, create the experiment, implement it on the appropriate branch, commit and bind the
exact source, launch only its declared invocation, inspect `hiraku runs <task> --json`, and propose a
conclusion using the task-declared metric directions. Use normal Git for accepted integration.

Do not commit, launch, change approved scope or evaluation, push, create or update a review, or merge
into a protected branch without the applicable approval. Never approve or merge a remote review.
Treat this program as workflow guidance, not a security boundary.
