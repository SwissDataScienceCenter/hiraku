# Running commands

Use `hiraku run` to execute a command locally in the foreground. The command inherits the terminal, streams output, accepts interactive input, and can be
stopped with Ctrl+C. Hiraku returns its exit code.
```bash
hiraku run --record experiment-1 -- uv run python train.py --epochs 20
```

Commands are not stored on records: pass the complete invocation after `--` each time and arguments are
passed as an argv list.

Hiraku creates an in-memory `Job` and executes it with `LocalJobExecutor`.
Its `JobSpec` contains the command, working directory, and environment.

Execution happens in the existing project working tree. Dirty and uncommitted
changes are available to the command. Hiraku injects `HIRAKU_JOB_ID`,
`HIRAKU_RECORD_ID`, `HIRAKU_COMMIT`, and `HIRAKU_GIT_STATE` into the child
environment so tracking integrations can attribute runs. The job ID exists
for the lifetime of this execution; tracking backends own their run IDs.

The tracking context manager reads those variables automatically:

```python
from hiraku import track_run

with track_run(backend="wandb", project="my-project") as run:
    run.log({"loss": 0.5})
```

Use `backend="mlflow"` for MLflow; its backend-specific options are forwarded
to `mlflow.start_run`.

An optional dispatcher can prepare repeatable project-specific runtime
behavior around the supplied command:

```bash
hiraku run --record experiment-1 --dispatch my-runtime -- uv run python train.py
```

Dispatchers receive `--` followed by the command argv. Hiraku remains in the foreground and reports the dispatcher's exit code.