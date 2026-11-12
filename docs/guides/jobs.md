# Jobs and executors

Hiraku uses native PSI/J jobs and executors. It adds record attribution and a
foreground output helper. Executors own submission and final-status reporting;
Hiraku does not maintain project job history.

## Run locally

```bash
hiraku run --record baseline -- python train.py
```

The command streams stdout and stderr, waits, and returns the process exit code.
Output is buffered through temporary files and removed afterward. Stdin is
closed. Local execution uses the caller's working directory, including uncommitted
changes; use `--directory` to choose another directory.

Without `--record`, CLI and MCP use the active record when set, otherwise the job
is unattributed. Commands are explicit argv, never record metadata.

## Submit and retrieve status

```bash
hiraku job submit --record baseline -- python train.py
```

Submission returns JSON containing `executor`, `executor_url`, `native_id`,
and the currently observed `status`. Retain the executor target and native ID
to retrieve the outcome, including from another process or outside the project:

```bash
hiraku job status NATIVE_ID --executor local-detached --executor-url FILE_URL
hiraku job status NATIVE_ID --executor local-detached --executor-url FILE_URL --wait --json
```

`status` attaches through PSI/J; `--wait` waits for a final state. Without it,
the reported state may still be `NEW` or pending while the executor observes the
backend. There is no project-level job list, saved submission receipt, or spec
snapshot. PSI/J's `executor.list()` discovers backend-native IDs where supported.

The default submission executor, `local-detached`, is a PSI/J
`LocalJobExecutor` subclass. It stores `result.json`, `stdout.log`, and
`stderr.log` in `<executor_url>/<native_id>/`. The default backend directory is
`~/.psij/work/detached/`; a `file://` executor URL selects another directory.
This is executor state on the submitting host. Keep it until the outcome and
output are no longer needed.

The detached launcher survives the submitting process and atomically records
the exit code and completion time. A missing result remains pending: process
disappearance is never interpreted as success. Detached execution requires
POSIX and Bash, supports a single process and PSI/J pre/post hooks, and does not
support cancellation. The native `local` executor does not recover exit codes
after the submitting process exits.

## Python

```python
import psij

from hiraku.execution import prepare_job, run_local
from hiraku.project import Project

project = Project.discover()
job = prepare_job(["python", "train.py"], project=project, record_id="baseline")
status = run_local(job)

job = prepare_job(["python", "train.py"], project=project, record_id="baseline")
executor = psij.JobExecutor.get_instance("local-detached")
executor.submit(job)
status = job.wait()

# In another process, reconstruct the executor using its name and URL:
attached = psij.Job()
executor.attach(attached, job.native_id)
status = attached.wait()
```

`prepare_job()` returns a `psij.Job` without submitting or writing files.
It accepts explicit `record_id`, `env`, `directory`, native PSI/J
`attributes`, and `resources`. Python callers select record attribution
explicitly; omission does not select the active record. The spec remains
available for normal PSI/J configuration, including output paths.
`run_local()` streams output and returns the native `JobStatus`; supplied
output files are retained.

Scripts receive `HIRAKU_RECORD_ID` (empty when unattributed) and
`HIRAKU_JOB_ID` (the PSI/J job's `id`, distinct from the backend `native_id`).
Log them as CSV `record_id` / `hiraku_job_id`, or MLflow tags and W&B config
values `hiraku.record_id` / `hiraku.job_id`. Both environment names are
reserved. A job may produce zero, one, or many runs. Tracking attribution,
logging destinations, and code provenance remain independent of executor state.

MCP exposes `job_submit` and `job_status` with the same executor target and
native ID as the CLI. Status requests return without waiting for completion.

## Renku

Install and authenticate `rnk` independently, then choose an existing launcher:

```bash
hiraku job submit --executor renku --executor-url https://renkulab.io/p/namespace/project \\
  --directory /path/to/remote/project --attribute renku.launcher LAUNCHER_ID \\
  -- python train.py
hiraku job status NATIVE_ID --executor renku \\
  --executor-url https://renkulab.io/p/namespace/project --wait --json
```

The executor URL identifies the Renku project. The launcher supplies the image,
mounts, environment, and resources; Hiraku does not upload local code or change
the launcher. For a Cloud Native Buildpacks image, use
`--attribute renku.command_prefix /cnb/lifecycle/launcher`.

The executor observes Renku states through `rnk job list` and reports final
PSI/J status for submitted and attached jobs. Renku's CLI does not expose process
exit codes, so `exit_code` remains unset, including for failed jobs. Missing jobs
and observation errors never imply completion. Logs and control remain available
through `rnk job logs` and `rnk job stop`. PSI/J resource requests, stream
redirection, and launch hooks are unsupported by this adapter.

## Executor extensions

Register executors using PSI/J descriptors in an installed
`psij-descriptors/*.py` module. The bundled implementations are
`src/psij-descriptors/local.py` and `src/psij-descriptors/renku.py`.
Do not add an `__init__.py` to the descriptor directory.
The bundled `quoted-single` launcher works around PSI/J's unquoted redirection
paths; `detached` additionally records the durable local outcome.
Other installed PSI/J executors, such as Slurm, retain their own backend
configuration and launcher behavior.
