# Bounded autoresearch program

This program adapts the loop in `karpathy/autoresearch` at the revision recorded in `UPSTREAM.md` to Hiraku's task, experiment, branch, run, and review model.

Before starting, obtain approval for the task scope, the single-GPU and time budgets, permitted commands, the maximum number of iterations or wall-clock stopping time, and whether commits and launches may proceed autonomously. Verify that the prepared data and tokenizer exist as described in the example README.

For each iteration:

1. Inspect the current task, experiment lineage, and ranked runs through Hiraku JSON.
2. State one hypothesis and create an experiment with `hiraku exp create <task>:<id> --branch [--from <ref>]`, declaring `gpu` as its entrypoint.
3. Verify the canonical experiment branch, then modify only `train.py`.
4. Commit the coherent implementation and bind the exact commit to the experiment.
5. Launch `gpu`, keeping the ten-minute hard timeout and single-GPU boundary.
6. Inspect `val_bpb`, peak VRAM, runtime, crashes, and commit mismatches. Record the conclusion and decision in the experiment.
7. For an accepted result, advance the task branch using normal Git within the approved boundary. For a rejected result, retain the branch and record; do not reset away provenance or delete it automatically.
8. Update `task.md`, including findings and a concise changelog entry.

Stop at the approved iteration or wall-clock limit, on repeated infrastructure failure, or when a scope/resource change would be required. Never alter `prepare.py`, the evaluation protocol, dependencies, or resource bounds without fresh approval. Never push, create/update a review, approve a review, or merge a protected branch without the applicable approval. `cpu-mock` is only for CI and workflow validation.
