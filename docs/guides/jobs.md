# Jobs and executors

Hiraku runs local commands and submits remote jobs through PSI/J. Each attempt
has a unique Job ID and belongs to the selected record:

```bash
hiraku job run --record baseline -- python train.py
hiraku job submit --record baseline -- python train.py
```

Without `--record`, the active record is used. Commands run in the project root,
including uncommitted changes. Foreground execution prints the job ID to stderr,
streams saved output, and returns the command's exit code. Background execution
prints the ID to stdout and returns after acceptance. `run` uses PSI/J's native
`local` executor and owns execution until completion. `submit` defaults to our
`detached` executor, whose worker runs independently of the submitting process.
An explicit `submit --executor local` uses native local execution in the foreground,
streaming and waiting just like `run`.
`run --no-follow` saves logs without streaming them. Commands have closed stdin;
interactive terminal applications are not supported.

For `submit`, `--executor NAME` selects any installed PSI/J executor.
`--executor-url URL`
passes its target URL, and repeated `--attribute KEY VALUE` options set PSI/J
custom attributes. `--directory PATH` sets the execution-side working directory;
the default is the project root for local jobs and the executor default otherwise.

```bash
hiraku job list [--json] [--cached]
hiraku job show JOB_ID [--json] [--cached]
hiraku job logs JOB_ID
hiraku job logs JOB_ID --follow
hiraku job wait JOB_ID
hiraku job cancel JOB_ID
```

`show` and `list` automatically observe nonfinal jobs through PSI/J attachment,
except native `local` jobs, whose owning foreground process publishes snapshots.
Observations query the selected backend and persist status
and logs where supported. Final jobs are left unchanged. Use `--cached` to read
saved observations without contacting a backend. Listing observes jobs sequentially;
Attachment is in-process and reused for subsequent updates of the same Job object;
there is no observation subprocess or JSON transport. Hiraku waits up to 30 seconds
for an initial PSI/J observation (plus up to one second for its lock). Backend
calls retain their own timeouts; Renku CLI calls allow 20 seconds each.
Our adapters synchronously refresh their observations on update. Other PSI/J
executors use their existing polling and status notifications; subsequent reads
return the latest status published by that executor.
Failed updates log a warning and preserve the saved status and `updated_at`.
Show/list continue with other jobs and return plain Job snapshots in JSON/MCP.
Terminal output marks nonfinal jobs `(stale)` after 30 seconds without a saved
update. Final jobs are never marked stale. `show` includes `updated_at`; JSON/MCP
clients can assess freshness from that timestamp.

For every executor, `submit` uses PSI/J and returns after backend acceptance.
Hiraku does not launch a generic submission runner. `wait` and
`logs --follow` observe jobs every second while those commands run. `wait` prints no logs
and returns the job's exit code; `logs` alone prints saved output and returns.
Observation failure stops waiting/following with an error, leaving the job running.

Cancellation attaches through PSI/J for detached and remote jobs. The detached
adapter requests cancellation from its worker. Native local jobs accept a request
that their owning foreground process handles.
Ctrl-C during foreground waiting
requests cancellation and waits for cleanup. Stopping log following does not
cancel a job. Interrupting `job wait` also stops observation only.

## Persistence

Each job lives in `.hiraku/jobs/<job_id>/`: `job.json`, `stdout.log`, and `stderr.log`.
Snapshots are replaced atomically. The launch process creates
the initial snapshot; subsequent writers use a per-job lock. Job metadata includes
the command, record, host, native ID, timestamps, exit status, and launch-time Git
commit and cleanliness. Launch provenance describes the submitting checkout,
not necessarily code loaded later by the command.

Only Hiraku writes `job.json`, at submission or after successful observation.
`updated_at` records that snapshot write; failed observations leave it unchanged.
Without an observing command, the snapshot can lag execution even for local jobs.

The detached adapter independently owns
`.hiraku/executors/detached/<native_id>/status.json`. Its worker retains
the built-in PSI/J local handle and records state, timestamps, and the real exit
code there. It writes command output directly to the configured log paths, never
to `job.json`. Execution specs and explicit environment additions travel over
stdin and are not stored in adapter state. Local backend state is retained for
later attachment, including after the worker exits.

A detached native ID is an opaque execution token, not a PID. A nonfinal backend
status older than 30 seconds is an observation error; disappearance is never
treated as success. A saved final outcome remains valid regardless of age.
Startup acknowledgment is bounded to 30 seconds. Do not delete backend state
while execution is active.

The adapter is registered as `detached`, leaving PSI/J's built-in `local` unchanged.
`executor_url` selects the adapter state directory (a path or file URI); by default
Hiraku uses the project path above. Standalone PSI/J callers can use
`JobExecutor.get_instance("detached", url=...)`; without a URL the adapter uses
`JobExecutorConfig.work_directory / "detached"`. Attachment must select the
same state directory. Nonfinal execution can only be observed on its original host.

Native local execution has no separate backend state file: its owning Hiraku
process retains the original PSI/J handle and updates `job.json`. Later readers
use that snapshot; they never attach to the PID to infer an exit code. Losing the
owner leaves a stale nonfinal snapshot, reported as an unknown outcome after
30 seconds. Background it using your shell only while retaining that process.

Remote jobs can outlive the submitting host. Later commands observe them using
PSI/J attachment. While a Job object is used, its attached PSI/J handle may poll;
our adapters stop their observation loops when that handle is discarded or final.
No persistent Hiraku remote-monitor process is started.

Job storage is local runtime state and should be excluded from Git with
`/.hiraku/`. Logs persist until explicitly removed by the user.

## Tracking attribution

Hiraku injects only `HIRAKU_JOB_ID`. Logging destinations and credentials remain
configured by your script or its environment. Only `HIRAKU_JOB_ID` is reserved;
other environment variables pass through unchanged. Record the job ID on each trace:

- CSV: `hiraku_job_id` column.
- MLflow: `hiraku.job_id` tag.
- W&B: `hiraku.job_id` config value.

A job may produce several traces or none. Leaderboards join traces with local
jobs before filtering by record, sorting metrics, or limiting results. A matched
Job determines `Run.record_id`. `Run.commit` retains the commit reported by the
tracking source, which may describe different code from the submitting checkout.
Launch provenance stays on `Job.commit`; load the linked Job using `Run.job_id`
when needed. Both commits may be absent.
Unknown or missing Job IDs preserve the trace's own record attribution; traces
without it remain unattributed. Malformed Job snapshots raise an error.
Existing directly attributed traces can still be queried without job history.

Python callers use blocking `Job.run(command, project=project)`,
`Job.submit(command, project=project, executor="renku", ...)`, `Job.load(id,
project=project)`, and `Job.list(project=project)` from `hiraku.jobs`.
A Job exposes PSI/J's `spec` (`JobSpec`) and `status` (`JobStatus`);
`job.wait()` returns a PSI/J status, `job.logs(follow=True)` yields output,
and `job.cancel()` requests cancellation. `Job.submit()` also accepts `executor`,
`executor_url`, `directory`, native PSI/J `attributes`, and `resources`.
`Job.run()` accepts local `directory`, `env`, `attributes`, and `resources`, and
blocks until completion. The CLI owns foreground streaming; the Python API does
not expose output callbacks. `Job.submit()` defaults to `executor="detached"`.
With explicit `executor="local"`, the caller must keep the process alive and
call `job.wait()` or `job.logs(follow=True)` to record native local completion.
The detached adapter composes the built-in executor, preserving PSI/J attributes
and resources.
`Job.load()` and `Job.list()` read snapshots without network access;
`job.update()` observes once and raises `ExecutionError` if observation fails,
leaving the saved snapshot unchanged. `job.status.state` is the sole execution
state. `update_jobs(jobs)` attempts updates independently and logs failures.
There are no additional observation states or result wrappers.
`--cached` reports the saved state without claiming a current backend observation.
State names follow PSI/J:
`NEW`, `QUEUED`, `ACTIVE`, `COMPLETED`, `FAILED`, and `CANCELED`.

MCP provides `job_submit` (default executor: `detached`), `job_list`,
`job_show` (both accepting `cached`), `job_wait`, `job_logs`,
and `job_cancel`. Submission returns Job metadata after startup/acceptance;
use `job_wait` and `job_logs` separately. Explicit `executor="local"` waits in the
tool call for foreground completion. There is no `background` switch.

## Renku through rnk

Install and authenticate the `rnk` CLI independently (`rnk login`). The bundled
`renku` executor uses that CLI for submission, polling, cancellation, and logs:

```bash
hiraku job submit --executor renku --executor-url https://renkulab.io/p/namespace/project \
  --attribute renku.launcher LAUNCHER_ID \
  -- python -u train.py
```

The existing launcher supplies the image, mounts, and resource allocation.
Alternatively, use the server URL and `--attribute renku.project namespace/project`.
Commands override only that submission, without modifying the launcher.
For a Cloud Native Buildpacks image, add
`--attribute renku.command_prefix /cnb/lifecycle/launcher` to initialize the image's
launch environment. Relative command paths resolve in the remote launcher's working
directory, not the local checkout. Hiraku does not upload local files or sync code.

The Renku submission key is a deterministic 20-character hash derived from the
Hiraku job ID, within rnk's length limit, for deduplication. The
returned Renku job name is PSI/J's `native_id` and is used for polling and logs.
Waiting and following update every second; the Renku adapter also publishes
background observations every five seconds while its handle is retained.
Transport failures and missing observations
are logged during observation; they do not establish a terminal
outcome. Renku's
combined application output is cached in `stdout.log`; separate stderr and
numeric exit codes are not exposed by rnk. A known successful terminal state
returns CLI exit code zero; the stored native exit code remains absent.

This initial adapter uses launcher resources and does not implement PSI/J resource
requests, staging, or launch hooks. It supports PSI/J `attach()` to observe an
existing native job; select its project in the executor URL or JobSpec attributes.
Remote jobs may outlive the local monitor; automatic Hiraku monitor recovery is
not implemented yet.

## Adding executors

Implement PSI/J's `JobExecutor` contract and provide a standard
`psij-descriptors/*.py` descriptor in the installed package. Hiraku discovers
executors through `JobExecutor.get_instance()` without a separate registry.
See `src/psij-descriptors/renku.py` for an implementation and its descriptor.
Each custom adapter can live in its own module here (for example, `runai.py`),
with its own `__PSI_J_EXECUTORS__` list. Do not add an `__init__.py` to this directory.
PSI/J's built-in Slurm executor is also discoverable; cluster access, shared
paths, and scheduler configuration must be supplied by the execution environment.
