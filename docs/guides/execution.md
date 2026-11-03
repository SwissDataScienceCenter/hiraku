# Running commands

This guide covers foreground execution with `hiraku job run`.

`hiraku job run` executes a command in the current project and waits
for it to finish. Hiraku follows its output files and returns the command's
exit code. This does not provide interactive stdin. Hiraku stores a job record
in `hiraku/jobs/`.

For asynchronous submission, `hiraku job submit` runs the command locally
and prints the job ID.

```bash
hiraku job submit --record experiment-1 -- python train.py
hiraku job status JOB_ID
hiraku job logs JOB_ID --follow
hiraku job wait JOB_ID
hiraku job cancel JOB_ID
```

Local jobs use PSI/J for status and exit codes. A foreground `jobs run` keeps
the original PSI/J job attached while it waits. A later `jobs status` or
`jobs wait` reattaches by native ID; PSI/J documents that on Linux, attached
local jobs can report exit code zero regardless of the actual code. Local
cancellation requires the process identity recorded at submission.

Hiraku assigns every job a `HIRAKU_JOB_ID`. The tracking context managers use
that ID to resolve the persisted job and attach its record, commit, and Git
state to the tracked run:

```python
from hiraku import track_run

with track_run(backend="wandb", project="my-project") as run:
    run.log({"loss": 0.5})
```

Use `backend="mlflow"` for MLflow; its backend-specific options are forwarded
to `mlflow.start_run`.
Tracking remains independent of execution. To find runs associated with a job,
load a `Job` from `hiraku.jobs` and call `job.tracked_runs(backend)` with the
desired tracking backend. A job can have zero or multiple tracked runs.

```bash
hiraku job run --record experiment-1 -- uv run python train.py --epochs 20
```

Without `--record`, Hiraku uses the active record. Commands are not stored on
records: pass the complete invocation after `--` each time. Arguments are
passed as an argv list; Hiraku does not evaluate a shell string. Use `sh -c`
explicitly when shell syntax is needed.

Execution happens in the existing project working tree. Dirty and uncommitted
changes are available to the command. `hiraku job run` and
`hiraku job submit` expose `HIRAKU_JOB_ID`; the tracking wrappers resolve the
remaining provenance from the saved job. The separate `execute_command`
Python API also provides the record and Git metadata as environment variables.
Tracking backends own their run IDs; the Hiraku job ID identifies the command
invocation.

An optional dispatcher can prepare repeatable project-specific runtime
behavior around the supplied command:

```bash
hiraku job run --record experiment-1 --dispatch slurm -- uv run python train.py
```

Dispatchers receive `--` followed by the command argv. Hiraku itself runs in
the foreground and reports the dispatcher's exit code.

The existing Python API also supports direct foreground execution:

```python
from hiraku.execution import execute_command

result = execute_command(
    ["python", "train.py", "--epochs", "20"], record_id="experiment-1"
)
if result.returncode:
    raise SystemExit(result.returncode)
```
