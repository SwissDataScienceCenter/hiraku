# Jobs

Hiraku provides an interface for executing and submitting jobs across different platforms.
It does not replace a job scheduler, rather it provides a unified interface for human and agents
to manage and trace job submission. Hiraku also adds record attribution and records a submission
history. Job execution is built on the [PSI/J library](https://exaworks.org/psij-python/index.html).

## Run locally

To run jobs locally in the foreground, use `hiraku run` with an optional record for attribution:

```bash
hiraku run --record baseline -- python train.py
```

Without `--record`, the CLI uses the active record when set, otherwise the job
is unattributed.
Hiraku passes `HIRAKU_RECORD_ID` and `HIRAKU_JOB_ID` environment variables to the command.

## Launchers and custom attributes

`run` and `job submit` accept `--launcher NAME` to select an installed PSI/J
launcher. Omit it to use the executor's default; local execution selects
`quoted-single`.

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

```bash
hiraku job submit --record baseline -- python train.py
```

Submission returns JSON with `id` (the original PSI/J job ID), plus its command
specification, record, executor target, and native ID of the backend.
Use that `id` to query the status of the submission:

```bash
hiraku job status JOB_ID
hiraku job status JOB_ID --json
```

## Submission history

Each accepted submission is saved locally to `.hiraku/jobs/<id>.json`.
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
from hiraku.job import Submission, get_executor, prepare_job, run_local
from hiraku.project import Project

project = Project.discover()
job = prepare_job(["python", "train.py"], project=project, record_id="baseline")
status = run_local(job, project=project)

job = prepare_job(["python", "train.py"], project=project, record_id="baseline")
executor = get_executor("local-detached", work_directory=project.psij_dir)
executor.submit(job)
submission = Submission.from_psi_job(job)
submission.save(project)
entries = Submission.list(project, record_id="baseline")

# In another process, attach and explicitly update the saved snapshot:
submission = Submission.load(project, submission.id)
attached = submission.get_psi_job()
status = attached.wait()
submission.sync(attached)
submission.save(project)
```

MCP exposes `job_submit`, `job_list(record_id=...)`, and mutating
`job_status(job_id=...)`, which syncs, saves, and returns the updated submission.
These use the same history and original PSI/J IDs as the CLI.
