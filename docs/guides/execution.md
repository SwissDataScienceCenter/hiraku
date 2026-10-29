# Running commands

This guide covers foreground execution with `hiraku run`.

`hiraku run` executes a command in the current project and waits for it to
finish. Output streams directly to the terminal, and Hiraku returns the
command's exit code. No execution receipt or status record is stored.

```bash
hiraku run --record experiment-1 -- uv run python train.py --epochs 20
```

If the selected record inherits a default command, omit the command after `--`:

```bash
hiraku run --record experiment-1
```

Without `--record`, Hiraku uses the active record. A command after `--`
replaces the inherited command. Arguments are passed as an argv list; Hiraku
does not evaluate a shell string. Use `sh -c` explicitly when shell syntax is
needed.

Execution happens in the existing project working tree. Dirty and uncommitted
changes are available to the command. Hiraku sets `HIRAKU_RECORD_ID`,
`HIRAKU_COMMIT`, and `HIRAKU_GIT_STATE`, along with a per-invocation
`HIRAKU_JOB_ID` used only to correlate tracking data. The invocation ID is not
a durable job and cannot be used to query status or logs later.

An optional dispatcher can prepare an environment around the command:

```bash
hiraku run --record experiment-1 --dispatch slurm -- uv run python train.py
```

Dispatchers receive `--` followed by the command argv. Hiraku itself runs in
the foreground and reports the dispatcher's exit code.

The Python API provides the same behavior:

```python
from hiraku import execute_command

result = execute_command(
    ["python", "train.py", "--epochs", "20"], record_id="experiment-1"
)
if result.returncode:
    raise SystemExit(result.returncode)
```

Older `hiraku job` lifecycle commands and the `Job`/`JobSpec` Python API were
removed. Run the command directly and inspect its tracking backend with
`hiraku runs` when it finishes.
