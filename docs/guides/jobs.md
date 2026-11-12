# Jobs and executors

Hiraku uses native PSI/J jobs and executors. It adds record attribution and a
foreground output helper. Executors own submission and final-status reporting;
Hiraku records submission details and saved status snapshots in project history.

## Run locally

```bash
hiraku run --record baseline -- python train.py
```

The command streams stdout and stderr, waits, and returns the process exit code.
Output is buffered through temporary files and removed afterward. Stdin is
closed. Local execution uses the caller's working directory, including uncommitted
changes; use `--directory` to choose another directory.

Without `--record`, the CLI uses the active record when set, otherwise the job
is unattributed. MCP requires an explicit `record_id` for attribution; omitting
it submits an unattributed job. Commands are explicit argv, never record metadata.

`run` and `job submit` accept `--launcher NAME` to select an installed PSI/J
launcher by its registered name. It sets `JobSpec.launcher` and is retained in
history. Omit it to use the executor's default; local execution selects
`quoted-single`. The selected executor must support the launcher.

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

## Submit and retrieve status

```bash
hiraku job submit --record baseline -- python train.py
```

Native `local` is reserved for `hiraku run`; asynchronous submission requires
an executor such as `local-detached` that can recover status after submission.

The detached supervisor saves its process identity before launching the command.
If that startup step fails, the accepted job reports failure without running the
command. The submitting process reaps the supervisor even if no outcome file
could be written.

Submission returns JSON with `id`, the original PSI/J job ID, plus its command
specification, record, executor target, native ID, and currently observed status.
Use that `id` to read saved history or explicitly refresh it:

```bash
hiraku job list
hiraku job list --record baseline --json
hiraku job status JOB_ID
hiraku job status JOB_ID --json
```

The list table shows 8-character ID prefixes, lengthened when displayed IDs
collide, and local submission times as `YYYY-MM-DD HH:MM:SS`. JSON retains full
IDs and ISO timestamps. CLI and MCP status accept full IDs or any nonempty unique
prefix; ambiguous prefixes require more characters.

`status` attaches through PSI/J, captures the currently available status, saves it,
and displays the updated submission without waiting for completion.
`list` refreshes and saves each nonfinal submission before displaying status.
Saved `COMPLETED`, `FAILED`, and `CANCELED` outcomes are not queried again.
The CLI table colors only the status value: completed is green, failed or
canceled is red, and other states are white. JSON includes status and observation
timestamps.
If a job cannot be observed, listing warns and returns `UNKNOWN` with an
`observation_error` in JSON/MCP output. `UNKNOWN` is white. This is response-only:
saved history and the last successful `observed_at` remain unchanged.
The text output includes the command with shell-quoted arguments.
Attachment itself may contact the backend and take time.
PSI/J's `executor.list()` independently discovers backend-native IDs where supported.

## Project history

Each accepted submission is saved atomically to `.hiraku/jobs/<id>.json`.
The file contains the original PSI/J job ID, record ID, submission timestamp,
executor name and URL, backend native ID, serialized `JobSpec`, `status`, and
`observed_at`, the UTC time that snapshot was captured.
No additional Hiraku submission ID is generated. Attaching creates
a new in-memory PSI/J job, but does not change the saved original ID.

Sync actions attach through PSI/J, copy the current native status, and save it.
Snapshots include state, status time, exit code, message, and metadata, including
pending states. List filters apply before nonfinal jobs are refreshed. Attachment
or status-reading failures raise `ObservationError` without changing the snapshot
or file. CLI `status` fails with an error; MCP `job_status` returns a tool error.
A final outcome is recoverable only while the backend retains it or after it has
been synced and saved. `observed_at` advances on every successful sync,
even when the state is unchanged. `Status.time` remains the executor-reported
transition time. Reading or saving alone does not advance `observed_at`.
Corrupt history files are reported as warnings and omitted from listings;
loading one directly reports an error.

Foreground CLI execution also records its submission and saves final status
before returning. Native local jobs cannot safely be reattached; later sync
requests raise `ObservationError`, leaving the saved snapshot readable. If the
foreground process crashes, no outcome is inferred from its PID.
History may reference temporary foreground output paths; those files are removed
after execution. Backend result files remain separate from project history.

History includes explicit environment values from the spec; inherited environment
values are not captured. Keep `/.hiraku/` excluded from Git. If history cannot be
saved after acceptance, the error identifies the submitted execution; do not
automatically resubmit it.

The default submission executor, `local-detached`, implements PSI/J's
`JobExecutor` interface. It stores `result.json`, `stdout.log`, `stderr.log`, and
launcher diagnostics in `launcher.log` under `<executor_url>/<native_id>/`.
The default backend directory is
`~/.psij/work/detached/`; a `file://` executor URL selects another directory.
This is executor state on the submitting host. Keep it until the outcome and
output are no longer needed.

The executor starts a small Python supervisor in a new process session. The
supervisor runs the selected PSI/J launcher, waits, and atomically records its
exit code and completion time, independently of the submitting process. Launch
failures record a message with no invented exit code. Before launching, the
supervisor records its PID and process creation time in `supervisor.json`. Sync reports
`ACTIVE` while that supervisor is alive. If it disappears without a result, the
job reports `FAILED` with no exit code and a message that the command may still
be running. Older submissions without supervisor identity remain pending until
a result appears. Process inspection failures are reported as observation errors.
Detached execution requires
POSIX and supports a single process and PSI/J pre/post hooks. The default
`quoted-single` launcher requires Bash; Python callers may select another
installed launcher through `job.spec.launcher`. Cancellation is unsupported.
The native `local` executor does not recover exit codes
after the submitting process exits.

## Python

```python
import psij

from hiraku.job import Submission, prepare_job, run_local
from hiraku.project import Project

project = Project.discover()
job = prepare_job(["python", "train.py"], project=project, record_id="baseline")
status = run_local(job, project=project)

job = prepare_job(["python", "train.py"], project=project, record_id="baseline")
executor = psij.JobExecutor.get_instance("local-detached")
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

`prepare_job()` returns a `psij.Job` without submitting or writing files.
It accepts explicit `record_id`, `env`, `directory`, `launcher`, native PSI/J
`attributes`, and `resources`. Python callers select record attribution
explicitly; omission does not select the active record. The spec remains
available for normal PSI/J configuration, including output paths.
`run_local()` streams output and returns the native `JobStatus`; supplied
output files are retained. Passing `project` enables history. Native Python
submissions opt in by converting an accepted job with `Submission.from_psi_job(job)`
and calling `submission.save(project)`. Conversion does not write files.
`Submission.load(project, job_id)` and `Submission.list(project, record_id=...)`
read validated metadata without contacting executors.
`Submission.from_prefix(project, prefix)` resolves a unique ID prefix and loads
that submission, rejecting missing or ambiguous matches. CLI and MCP list handlers
call `observe_submission(entry, project)` for each loaded submission. This helper
refreshes and saves nonfinal jobs and returns response-only `UNKNOWN` and
`observation_error` on observation failures, preserving saved history. Final
outcomes are not refreshed. Callers handle warnings; storage errors propagate.
`get_psi_job()` reconstructs
the executor and attaches without waiting or changing saved metadata. `sync()`
attaches internally, checks identity, and copies current status. Pass an existing
native job to `sync(job)` to use its in-memory status, including foreground local
jobs. Sync does not wait or write files; call `save(project)` explicitly after
success. `ObservationError` is an `ExecutionError` subclass; storage errors remain
separate failures of `save()`. PSI/J owns lifecycle
operations such as waiting and cancellation.
`Status` validates both observed and persisted status data. Convert native status
with `Status.from_psi_status(status)` and serialize with `status.model_dump()`.
`Submission.status` is the last explicitly saved observation, which may be pending.

Scripts receive `HIRAKU_RECORD_ID` (empty when unattributed) and
`HIRAKU_JOB_ID` (the PSI/J job's `id`, distinct from the backend `native_id`).
Log them as CSV `record_id` / `hiraku_job_id`, or MLflow tags and W&B config
values `hiraku.record_id` / `hiraku.job_id`. Both environment names are
reserved. A job may produce zero, one, or many runs. Tracking attribution,
logging destinations, and code provenance remain independent of executor state.

MCP exposes `job_submit`, `job_list(record_id=...)`, and mutating
`job_status(job_id=...)`, which syncs, saves, and returns the updated submission.
These use the same history and original PSI/J IDs as the CLI. List automatically
refreshes and saves nonfinal jobs; final outcomes use saved history.
Waiting remains available through native
PSI/J `job.wait()` in Python.
`job_submit` accepts an optional `launcher` registered name, like the CLI.

## Renku

Install and authenticate `rnk` independently, then choose an existing launcher:

```bash
hiraku job submit --executor renku --executor-url https://renkulab.io/p/namespace/project \
  --directory /path/to/remote/project --custom-attribute renku.launcher LAUNCHER_ID \
  -- python train.py
hiraku job status JOB_ID --json
```

The executor URL identifies the Renku project. The launcher supplies the image,
mounts, environment, and resources; Hiraku does not upload local code or change
the launcher. For a Cloud Native Buildpacks image, use
`--custom-attribute renku.command_prefix /cnb/lifecycle/launcher`.

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
paths. Detachment and result recording belong to the executor and its private
`_local_supervisor.py` helper, not to a separate PSI/J launcher. Attachments use
per-job observers; the submitting executor also reaps its supervisor process.
Other installed PSI/J executors, such as Slurm, retain their own backend
configuration and launcher behavior.
