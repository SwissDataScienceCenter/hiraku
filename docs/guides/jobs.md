# Jobs and executors

Hiraku uses native PSI/J jobs and executors. It adds record attribution and a
foreground output helper. Executors own submission and final-status reporting;
Hiraku records submission details and observed final outcomes in project history.

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

Submission returns JSON with `id`, the original PSI/J job ID, plus its command
specification, record, executor target, native ID, and currently observed status.
Use that `id` for project history and status lookup:

```bash
hiraku job list
hiraku job list --record baseline --json
hiraku job status JOB_ID
hiraku job status JOB_ID --wait --json
```

`list` shows jobs newest first, with commands and refreshed statuses. `status`
looks up the saved executor and attaches through PSI/J; `--wait` waits for a final
state. Otherwise, a bounded initial observation may still report `NEW` or pending.
PSI/J's `executor.list()` independently discovers backend-native IDs where supported.

## Project history

Each accepted submission is saved atomically to `.hiraku/jobs/<id>.json`.
The file contains the original PSI/J job ID, record ID, submission timestamp,
executor name and URL, backend native ID, serialized `JobSpec`, and optional
`final_status`. No additional Hiraku submission ID is generated. Attaching creates
a new in-memory PSI/J job, but does not change the saved original ID.

List and status queries persist native final statuses when observed, including
state, status time, exit code, message, and metadata. Saved final outcomes need
no backend access. Pending statuses and observation errors are not written back.
An unavailable backend reports `UNKNOWN` with `observation_error`, preserving
the submission and other jobs' results. A final outcome is recoverable only while
the backend retains it or after Hiraku has observed and saved it.

Foreground CLI execution also records its submission and saves final status
before returning. While its final status is unavailable, history reports
`UNKNOWN`: native local attachment cannot safely recover the real exit code.
If the foreground process crashes, no outcome is inferred from its PID.
History may reference temporary foreground output paths; those files are removed
after execution. Backend result files remain separate from project history.

History includes explicit environment values from the spec; inherited environment
values are not captured. Keep `/.hiraku/` excluded from Git. If history cannot be
saved after acceptance, the error identifies the submitted execution; do not
automatically resubmit it.

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

from hiraku.job import (
    list_submissions, observe_submission, prepare_job, run_local, save_submission,
)
from hiraku.project import Project

project = Project.discover()
job = prepare_job(["python", "train.py"], project=project, record_id="baseline")
status = run_local(job, project=project)

job = prepare_job(["python", "train.py"], project=project, record_id="baseline")
executor = psij.JobExecutor.get_instance("local-detached")
executor.submit(job)
save_submission(job, project=project)
result = observe_submission(job.id, project=project, wait=True)
entries = list_submissions(project=project, record_id="baseline")

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
output files are retained. Passing `project` enables history. Native Python
submissions opt in with `save_submission()` after successful submission.
`load_submission()` reads metadata without contacting executors;
`observe_submission()` and `list_submissions()` refresh pending jobs and save
observed final statuses. These functions return dictionaries, not live job wrappers.

Scripts receive `HIRAKU_RECORD_ID` (empty when unattributed) and
`HIRAKU_JOB_ID` (the PSI/J job's `id`, distinct from the backend `native_id`).
Log them as CSV `record_id` / `hiraku_job_id`, or MLflow tags and W&B config
values `hiraku.record_id` / `hiraku.job_id`. Both environment names are
reserved. A job may produce zero, one, or many runs. Tracking attribution,
logging destinations, and code provenance remain independent of executor state.

MCP exposes `job_submit`, `job_list(record_id=...)`, and `job_status(job_id=...)`
using the same project history and original PSI/J IDs as the CLI. MCP status
requests return without waiting for completion.

## Renku

Install and authenticate `rnk` independently, then choose an existing launcher:

```bash
hiraku job submit --executor renku --executor-url https://renkulab.io/p/namespace/project \
  --directory /path/to/remote/project --attribute renku.launcher LAUNCHER_ID \
  -- python train.py
hiraku job status JOB_ID --wait --json
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
