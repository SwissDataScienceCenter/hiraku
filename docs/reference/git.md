# Git and execution

Task and experiment records can refer to Git branches. Creating records
through the Python data API does not itself create or switch branches.
The CLI provides optional branch-management flags; see the
[generated reference](../api/cli.md).

## Bind an experiment to source

`hiraku experiment create ... --commit HEAD` resolves HEAD to a full commit
hash and stores it on the experiment. An attributed CLI launch uses that
commit automatically, creating a temporary detached worktree and removing it
after execution.

Later edits in your original checkout do not change the source of that run.
Commit changes and explicitly update the experiment's binding, or register a
new experiment to preserve distinct attempts. The current model does not
freeze an experiment permanently on first launch.

## Working-directory execution

Without a resolved commit, the launcher runs in the project root. It does
not require a clean checkout and does not assign an implicit HEAD commit.
This is useful for iteration, but does not identify an immutable source
snapshot.

The CLI accepts an explicit commit before the target:

```bash
hiraku launch --commit HEAD my-experiment:evaluate
```

For an experiment already bound to a commit, the explicit commit must match
that binding.

## Execution and tracking

The dispatcher launches an argument vector without shell expansion.
Hiraku forwards attribution such as `HIRAKU_TASK_ID`,
`HIRAKU_EXPERIMENT_ID`, and, for committed execution, `HIRAKU_COMMIT`.
A nonzero child exit is propagated by the CLI.

Logging remains the training program's responsibility. The launcher does
not guarantee a tracking row on failure or automatically create a backend run.
