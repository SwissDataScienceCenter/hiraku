# Jobs

Hiraku provides an interface for executing and submitting jobs across different platforms.
It does not replace a job scheduler, rather it provides a unified interface for human and agents
to manage and trace job submission. Hiraku also adds record attribution and records a submission
history. Job execution is built on the [PSI/J library](https://exaworks.org/psij-python/index.html).

## Run locally

For agent-launched commands, prefer `job_submit` through MCP when available;
it submits in the background and returns an ID. Use `job_logs` in MCP or
`hiraku job logs` in the CLI to read output, and `job_cancel` or
`hiraku job cancel` to stop a running job. Use `hiraku run` to run a command
locally in the foreground when streaming output and the command's exit code are needed:

```bash
hiraku run --record baseline -- python train.py
```

Without `--record`, the job is unattributed. MCP always requires an explicit
`record_id` for attribution.
Hiraku passes `HIRAKU_RECORD_ID` and `HIRAKU_JOB_ID` environment variables to the command.

## Launchers and custom attributes

`run` and `job submit` accept `--launcher NAME` to select an installed PSI/J
launcher. Hiraku defaults to `quoted-single` for local execution and Slurm
submissions. Other executors select their own default.

```bash
hiraku run --record baseline --launcher quoted-single -- python train.py
hiraku job submit --launcher quoted-single -- python train.py
```

Launcher names are not script paths. Project wrapper scripts remain part of the
command argv.

Use repeated `--custom-attribute KEY VALUE` options to populate PSI/J's
`JobAttributes.custom_attributes` with executor-specific settings. MCP
`job_submit` accepts the equivalent `custom_attributes` mapping. Standard PSI/J
job attributes and resources remain available through the Python API.

## Submit jobs

Use `hiraku job submit` to submit a job to a local or remote compute backend. By default,
submission uses the `local-detached` executor, which runs the command in a detached process and saves its final status. The CLI can also submit to other executors, such as Renku or Slurm, if they are installed and configured.

??? note "Detached output"
    By default, stdout, stderr, and launcher diagnostics are stored in
    `.hiraku/psij/detached/<native_id>/` as `stdout.log`, `stderr.log`, and
    `launcher.log`. A `file://` executor URL selects a different directory.

```bash
hiraku job submit --record baseline -- python train.py
```

The CLI returns JSON with `id` (the original PSI/J job ID), command
specification, record, executor target, and backend native ID. MCP returns a
compact receipt with the ID, record ID, executor, and initial state. Use the ID
to inspect or cancel the submission:

```bash
hiraku job status JOB_ID
hiraku job status JOB_ID --json
hiraku job logs JOB_ID
hiraku job logs JOB_ID --json
hiraku job cancel JOB_ID
```

## Submission history

Each accepted submission is saved locally to `.hiraku/jobs/<id>.json`.
The Python API exposes this directory as `Project.jobs_dir`. Listing submissions
skips unreadable or invalid history entries and emits a Python `RuntimeWarning`
for each skipped entry.
View the submission history with `hiraku job list`:

```bash
hiraku job list
hiraku job list --record baseline --json
```

The submission history records the original PSI/J job ID, record ID, submission timestamp,
`executor`, `executor_url`, `executor_work_directory`, backend native ID,
serialized `JobSpec`, `status`, and `observed_at`, the UTC time that snapshot
was captured.

??? note "Local detached submission"
    The default submission executor, `local-detached`, implements PSI/J's `JobExecutor` interface.
    The executor starts a small Python supervisor in a new process session. The
    supervisor runs the selected PSI/J launcher, waits, and atomically records its
    exit code and completion time, independently of the submitting process. Launch
    failures record a message with no invented exit code. Before launching, the
    supervisor records its PID and process creation time in `supervisor.json`. Sync reports
    `ACTIVE` while that supervisor is alive. If it disappears without a result, the
    job reports `FAILED` with no exit code and a message that the command may still
    be running.

## Slurm

PSI/J provides a native `slurm` executor. Submit a batch job with:

```bash
hiraku job submit --executor slurm --record baseline -- python train.py
```

Pass Slurm options as custom attributes prefixed with `slurm.`; PSI/J renders
them as `#SBATCH` directives. For example:

```bash
hiraku job submit --executor slurm --record baseline \
  --custom-attribute slurm.partition gpu \
  --custom-attribute slurm.time 01:00:00 \
  --custom-attribute slurm.account my-account \
  -- python train.py
```

Standard PSI/J job attributes and `ResourceSpec` fields also map to Slurm
options. The executor requires `sbatch`, `squeue`, and `scancel` where Hiraku
submits and observes jobs.

Slurm writes command output to `.hiraku/psij/slurm/<id>.stdout` and
`.hiraku/psij/slurm/<id>.stderr` on the shared project filesystem. Use
`hiraku job logs <id>` or MCP `job_logs` to read both streams. Hiraku also
keeps PSI/J's auxiliary files so later status checks can recover the command's
exit code. Status checks may wait briefly for Slurm's first update after
attaching. Once Hiraku has saved a terminal status, later checks read it from
history. With PSI/J 0.9.11, use a project path without spaces so Slurm's
generated batch script can write its auxiliary files.

## Renku

The `renku` executor talks directly to the Renku data services API.
Login uses the OAuth2 device flow: Hiraku prints a URL
and code on stderr, and tokens are cached (mode `0600`) in
`~/.config/hiraku/renku-api-tokens.json` and refreshed automatically.

Choose an existing launcher:

```bash
hiraku job submit --executor renku --executor-url https://renkulab.io/p/namespace/project \
  --directory /path/to/remote/project --custom-attribute renku.launcher LAUNCHER_ID \
  -- python train.py

hiraku job status JOB_ID
```

!!! warning "Remote execution"
    The launcher, command and training script must be present in the Renku environment.
    Hiraku does not upload local code or change the launcher.

## Python

```python
from hiraku.job import Submission, prepare_job, run_local, submit_job
from hiraku.project import Project

project = Project.discover()
job = prepare_job(["python", "train.py"], project=project, record_id="baseline")
status = run_local(job, project=project)

job = prepare_job(["python", "train.py"], project=project, record_id="baseline")
submission = submit_job(job, project=project, executor="local-detached")
entries = Submission.list(project, record_id="baseline")

# In another process, attach and explicitly update the saved snapshot:
submission = Submission.load(project, submission.id)
attached = submission.get_psi_job()
status = attached.wait()
submission.sync(attached)
submission.save(project)
```

MCP exposes `job_submit`, `job_list(record_id=...)`, `job_status(job_id=...)`,
`job_logs(job_id=...)`, and `job_cancel(job_id=...)`. Cancellation saves the
updated status and returns only the resolved job ID and resulting state. These
use the same history and original PSI/J IDs as the CLI. The default
`local-detached` executor stops the job's process
group; other executors use their PSI/J cancellation support.
The MCP `job_list` result includes every matching job as a summary with its ID,
record ID, executor, and state. Use `job_status` for the exit code and message,
and `job_logs` for command output. Record listings similarly return all matching
record metadata without Markdown; use `record_show` to read a record's Markdown.
The MCP `job_status` result includes only the job ID and the status state, exit
code, and message; the CLI retains its detailed output. MCP `job_submit` returns
a compact receipt with the ID, record ID, executor, and initial state; the full
submission remains saved in project history.
