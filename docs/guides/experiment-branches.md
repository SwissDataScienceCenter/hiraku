# Experiment branches

In the simplest case, the user creates a task and associated working branch (e.g., `main` or `hiraku/tasks/{task_id}`). Agents are instructed to commit experiment code to the task branch, or (in a future version, a harness might enforce this as a hard constraint. To reject an experiment, the user (or agent) can choose to revert or reset code changes using the standard git commands.

Experiment branches allow advanced workflows for creating a DAG over experiments. Users or agents can explore several ideas in parallel using git worktrees, and backtrack or branch from any experiment. To associate an experiment with a branch use
```bash
hiraku experiment create ... --branch [BRANCH] [--from REF]
```
Without a value, `--branch` selects `hiraku/experiments/{task_id}/{experiment_id}`; a value selects any valid new or existing branch. The selected branch is recorded in the experiment. `--from` may resolve another branch or commit, defaults to task-branch HEAD, and is used only when creating a branch. Without `--branch`, the experiment remains on the task branch.

The user (or agent) is responsible to merge experiment branches into the task branch, and resolving conflicts. In particular, experiment records should be committed to the task branch so that they can be listed by Hiraku.

> We do not explicitly record experiment lineage. Since experiments refer to a unique commit, experiment lineage is a DAG derived from Git commit history. To be tested, subject to change.
