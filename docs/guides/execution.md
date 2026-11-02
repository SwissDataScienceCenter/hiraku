# Running commands

This guide covers foreground execution with `hiraku run`.

`hiraku run` executes a command in the current project and waits for it to
finish. Output streams directly to the terminal, and Hiraku returns the
command's exit code. No execution receipt or status record is stored.

```bash
hiraku run --record experiment-1 -- uv run python train.py --epochs 20
```

`--record` is required. Commands are not stored on
records: pass the complete invocation after `--` each time. Arguments are
passed as an argv list; Hiraku does not evaluate a shell string. Use `sh -c`
explicitly when shell syntax is needed.

Execution happens in the existing project working tree. Dirty and uncommitted
changes are available to the command. Hiraku sets `HIRAKU_RECORD_ID`,
`HIRAKU_COMMIT`, and `HIRAKU_GIT_STATE` for provenance. The tracking backend
creates and owns its run IDs; Hiraku does not create a second identifier for
the command invocation.

Git state is captured at launch across the repository. Tracked changes and
untracked files make it dirty; Git-ignored files do not. A clean tree does not
capture ignored data or dependencies.

An optional dispatcher can prepare repeatable project-specific runtime
behavior around the supplied command:

```bash
hiraku run --record experiment-1 --dispatch slurm -- uv run python train.py
```

Dispatchers receive `--` followed by the command argv. Hiraku itself runs in
the foreground and reports the dispatcher's exit code.

The Python API provides the same behavior:

```python
from hiraku.execution import execute_command

result = execute_command(
    ["python", "train.py", "--epochs", "20"], record_id="experiment-1"
)
if result.returncode:
    raise SystemExit(result.returncode)
```
