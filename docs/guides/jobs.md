# Jobs and executors

Hiraku launches commands through PSI/J (the local executor by default). Each attempt
has a unique Job ID and belongs to the selected record:

```bash
hiraku job run --record baseline -- python train.py
hiraku job submit --record baseline -- python train.py
```

Without `--record`, the active record is used. Commands run in the project root,
including uncommitted changes. Foreground execution prints the job ID to stderr,
streams saved output, and returns the command's exit code. Background execution
prints the ID to stdout and returns after submission. Commands have closed stdin;
interactive terminal applications are not supported.

`--executor NAME` selects any installed PSI/J executor. `--executor-url URL`
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

`show` and `list` automatically observe nonfinal jobs. Local observations come
from the owner snapshot; remote observations query the backend and persist status
and logs where supported. Final jobs are left unchanged. Use `--cached` to read
saved observations without contacting a backend. Listing observes jobs sequentially;
each remote query has a 40-second timeout (plus up to one second for its lock).
On failure, output shows `UNKNOWN`, the last known state, and an observation error
(in `show` and JSON). The last successful execution state is preserved in storage.

Remote `submit` returns without an ongoing monitor. `run`, `wait`, and
`logs --follow` poll remote jobs while those commands run. `wait` prints no logs
and returns the job's exit code; `logs` alone prints saved output and returns.
Observation failure stops waiting/following with an error, leaving the job running.

Cancellation uses the owning worker when present, or attaches to a remote job.
Ctrl-C during foreground waiting
requests cancellation and waits for cleanup. Stopping log following does not
cancel a job. Interrupting `job wait` also stops observation only.

## Persistence

Each job lives in `.hiraku/jobs/<job_id>/`: `job.json`, `stdout.log`, `stderr.log`,
and `worker.log`. Snapshots are replaced atomically. The launch process creates
the initial snapshot; subsequent writers use a per-job lock. Job metadata includes
the command, record, host, native ID, timestamps, exit status, and launch-time Git
commit and cleanliness. Launch provenance describes the submitting checkout,
not necessarily code loaded later by the command.

Local `run` and `submit` use the same execution path: an owner retains the
original PSI/J handle until completion to collect reliable exit codes after the
submitting CLI exits. If that owner is lost, Hiraku reports an
unknown outcome rather than guessing an exit code from a PID. Waiting detects
local monitoring loss after 30 seconds without a heartbeat.

Remote jobs can outlive the submitting host. Later commands observe them using
PSI/J attachment; no persistent remote monitor is started.

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

Python callers use `Job.submit(command, project=project)`, `Job.load(id,
project=project)`, and `Job.list(project=project)` from `hiraku.jobs`.
A Job exposes PSI/J's `spec` (`JobSpec`) and `status` (`JobStatus`);
`job.wait()` returns a PSI/J status, `job.logs(follow=True)` yields output,
and `job.cancel()` requests cancellation. `Job.submit()` also accepts `executor`,
`executor_url`, `directory`, native PSI/J `attributes`, and `resources`.
`Job.load()` and `Job.list()` read snapshots without network access;
`job.update()` observes once and raises `ExecutionError` if observation fails,
leaving the saved snapshot unchanged. `job.status.state` is the sole execution
state. CLI/MCP responses add transient `state` and `observation_error` fields:
`state` is `UNKNOWN` when that query fails, while nested `status` holds the last
successful PSI/J observation. These presentation fields are never persisted.
`--cached` reports the saved state without claiming a current backend observation.
State names follow PSI/J:
`NEW`, `QUEUED`, `ACTIVE`, `COMPLETED`, `FAILED`, and `CANCELED`.

MCP provides `job_submit` (with optional `background`), `job_list`,
`job_show` (both accepting `cached`), `job_wait`, `job_logs`,
and `job_cancel`.

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
Waiting and following poll every five seconds. Transport failures and missing observations
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
