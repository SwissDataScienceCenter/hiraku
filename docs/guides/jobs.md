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
prints the ID to stdout and returns after acceptance. Both use the `detached`
executor, whose worker runs independently of the submitting process. `run` adds
streaming and waiting; `submit --executor local` is an alias for `detached`.
`run --no-follow` saves logs without streaming them. Commands have closed stdin;
interactive terminal applications are not supported.

For `submit`, `--executor NAME` selects any installed PSI/J executor.
`--executor-url URL`
passes its target URL, and repeated `--attribute KEY VALUE` options set PSI/J
custom attributes. `--directory PATH` sets the execution-side working directory;
the default is the project root for local jobs and the executor default otherwise.

```bash
hiraku job list [--json]
hiraku job show JOB_ID [--json]
hiraku job logs JOB_ID
hiraku job logs JOB_ID --follow
hiraku job wait JOB_ID
hiraku job cancel JOB_ID
```

`show` and `list` display the executor's latest observation, or a saved final
outcome without contacting the backend. Executors own polling; Hiraku reuses
their handles without forcing extra queries. Initial attachment errors log a
warning; show/list continue with submission information for that job. Listing
attaches sequentially, allowing up to 30 seconds for an initial PSI/J status.

`wait` and `logs --follow` read executor status every second. `wait` prints no
logs and returns the job's exit code; `logs` alone prints saved output and returns.
Initial attachment failure stops waiting/following with an error, leaving the job
running. Later polling failures are logged by the executor and retain its last
observation; they do not establish a terminal outcome.

Cancellation uses the executor. Ctrl-C during foreground execution requests
cancellation and waits for cleanup. Interrupting log following or `job wait`
stops observation without canceling the job.

## Persistence

Each job lives in `.hiraku/jobs/<job_id>/`: `job.json`, `stdout.log`, and `stderr.log`.
Metadata includes the command, record, host, executor, native ID, timestamps, exit
status, and launch-time Git commit and cleanliness. Launch provenance describes
the submitting checkout, which may differ from code loaded by the command.

Hiraku atomically writes `job.json` at creation, submission, and when it observes
a final outcome. Intermediate observations remain in memory. Submission and
final-outcome writes use a per-job lock; backend queries do not hold it. A saved final outcome
remains available after backend history is removed. Without a Hiraku observer,
completion remains in the backend until a later command attaches and saves it.
`updated_at` is the last snapshot write, not an indication of backend freshness.

The detached executor keeps worker status under
`.hiraku/executors/detached/<native_id>/`. Its worker owns the built-in PSI/J local
handle and records the real exit code independently of Hiraku. Keep this state
while jobs are active; attachment requires the same directory and host. A worker
heartbeat older than 30 seconds is an observation error. Worker startup allows
30 seconds. `executor_url` can select another directory or file URI.

Later commands attach again to resume observation. Executor monitors stop when
their handles are discarded or final.

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

Python callers create an attempt before executing it:

```python
from hiraku.jobs import Job

job = Job.create(["python", "train.py"], project=project)
job.submit()
status = job.wait()

# Or create and wait in one expression:
job = Job.create(["python", "train.py"], project=project).run()
```

`Job.create()` validates and saves a `NEW` attempt without contacting an executor.
It accepts `record_id`, `env`, `executor`, `executor_url`, `directory`, native
PSI/J `attributes`, and `resources`. The default executor is `detached`;
`local` selects the same adapter, which preserves PSI/J attributes and resources.
Explicit environment additions stay in memory; submit the created object to
retain them. They are not stored in snapshots.
`job.submit()` and `job.run()` both return the job and allow only one submission
per attempt, including failed attempts. Retry with a new `Job.create()`.
`job.run()` submits using the configured executor, waits, and cancels on
interruption. It does not stream output; the CLI owns foreground streaming.
A Job exposes PSI/J's `spec` (`JobSpec`) and `status` (`JobStatus`);
`job.wait()` returns a PSI/J status, `job.logs(follow=True)` yields output,
and `job.cancel()` requests cancellation.
`Job.load()` and `Job.list()` read snapshots without network access;
`job.update()` reads the attached executor's latest status and saves final outcomes.
It raises `ExecutionError` if initial attachment or final persistence fails.
`update_jobs(jobs)` attempts updates independently and logs failures.
State names follow PSI/J:
`NEW`, `QUEUED`, `ACTIVE`, `COMPLETED`, `FAILED`, and `CANCELED`.

MCP provides `job_submit`, `job_list`, `job_show`, `job_wait`, `job_logs`, and
`job_cancel`. Submission returns after acceptance; use wait and logs separately.

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

The Renku adapter polls every five seconds while its handle is retained;
waiting and following read those observations without extra backend queries.
Each `rnk` call allows 20 seconds. Combined output is saved in `stdout.log`;
separate stderr and numeric exit codes are not exposed by rnk. A known successful
terminal state returns CLI exit code zero; the stored native exit code remains absent.

The adapter uses launcher resources; PSI/J resource requests, staging, and launch
hooks are unsupported.

## Adding executors

Implement PSI/J's `JobExecutor` contract and register a descriptor in an installed
`psij-descriptors/*.py` module, following `src/psij-descriptors/renku.py`.
Hiraku discovers executors through `JobExecutor.get_instance()`. Do not add an
`__init__.py` to the descriptors directory. Standalone PSI/J callers can use
`detached` with a state-directory URL; without one it uses
`JobExecutorConfig.work_directory / "detached"`.
PSI/J's built-in Slurm executor is also discoverable; cluster access, shared
paths, and scheduler configuration must be supplied by the execution environment.
