# Jobs and executors

A Job records one command execution attempt and belongs to a research record.
Run locally in the foreground, or submit an independent local execution:

```bash
hiraku job run --record baseline -- python train.py
hiraku job submit --record baseline -- python train.py
```

Without `--record`, the active record is used when set; otherwise the job is
unattributed. Local commands run in the caller's working directory, including
uncommitted changes. `run` uses PSI/J's native
`LocalJobExecutor`; local `submit` defaults to Hiraku's `local-detached` executor.
Commands have closed stdin. Git provenance belongs to records and user-managed
tracking runs; Jobs do not collect it.

`run` prints the job ID to stderr, waits, and returns the command's exit code.
Stdout and stderr are streamed to the terminal and saved to files. Stdin is
disconnected, so interactive commands are not supported.
Ctrl-C requests cancellation and waits for the local process to stop.
Hiraku saves the native final status in `job.json`. If Hiraku crashes before
saving it, the outcome remains unknown.

`submit` returns its job ID on stdout after the executor starts the launcher.
For `submit`, the local launcher creates a new session, runs the command, and
atomically writes `result.json` before exiting. It survives the submitting process
exiting without a Python worker, reattachment, or an observation monitor.
Default local submission requires a POSIX system with Bash and supports a single
command with PSI/J pre/post hooks;
the detached launcher does not support multiple-process resource requests.

For submission, `--executor NAME` selects an installed PSI/J executor,
`--executor-url URL` selects its target, and repeated `--attribute KEY VALUE`
options supply custom attributes. `--directory PATH` sets the execution-side
working directory. When omitted, the executor chooses the working directory.
Local jobs inherit the submitting process's directory; remote jobs use their
execution environment's default. Explicit paths refer to the execution side.

## Results and output

```bash
hiraku job list [--json]
hiraku job show JOB_ID [--json]
```

`show` and `list` read saved metadata and attach to detached local jobs through
their executor. Until the result appears, they show submission-time
information. If the launcher or machine fails, a missing result means the outcome is unknown;
Hiraku does not infer success from a missing process or invent a failure.

Inspect `stdout.log` and `stderr.log` in the job's directory using ordinary file
tools. Foreground `run` also streams these files; `submit` does not follow or
fetch output.

Cancellation from another process is not supported. Use the remote platform's
native controls for remote jobs. Renku logs are retrieved with `rnk job logs
NATIVE_ID`, rather than copied into Hiraku.
Killing a detached local launcher can prevent it from recording an outcome.
Foreground cancellation is saved by the waiting Hiraku process.

## Python and MCP

```python
from hiraku.jobs import Job

job = Job.create(["python", "train.py"], record_id="baseline", project=project).run()
print(job.status.exit_code)

job = Job.create(["python", "train.py"], record_id="baseline", project=project)
native = job.submit(executor="local-detached")  # Live PSI/J handle
# Later, read the saved result:
job = Job.load(job.id, project=project)
```

`Job.create()` validates and constructs an unsaved `NEW` attempt. It accepts
an optional `record_id`, `project`, `env`, `directory`, native PSI/J
`attributes`, and `resources`.
Only CLI and MCP resolve the active record. Python callers may supply an explicit
ID or omit it for an unattributed job.
CLI and MCP submission default to `local-detached`. `directory` is unset by
default and can be supplied for the execution environment. Explicit environment additions are
stored in `job.spec.environment` and persisted in `job.json`; inherited environment
values are not captured. The prepared spec is passed directly to PSI/J.

`run()` and `submit()` save the attempt before launching and
permit one execution per attempt, including failed attempts. Retry by creating
another Job. `run()` returns the Hiraku Job, uses native `LocalJobExecutor` through
`submit()`, streams its output, waits on the native
handle, and saves its final status, including cancellation on interruption.
The saved executor name is `local`. `submit(executor=...)` requires a PSI/J
executor name or instance and returns the PSI/J job after acceptance. The handle reports live
status while its executor is running; waiting on it does not itself save the final
status to the Hiraku snapshot. Detached local submission records its result separately.
When no launcher is specified, `run()` selects `quoted-single` and the
`local-detached` executor selects `detached`. Detached execution rejects other
launchers and requires output inside its configured job directory.
Selecting the native `local` executor directly for `submit()` uses PSI/J's
ordinary launcher and does not provide detached result recording.

`job.executor` is `None` until submission, then holds the selected PSI/J executor.
Snapshots retain the string descriptor and executor URL; loading reconstructs
the instance. For `local-detached`, Hiraku configures the URL to the jobs directory
and uses the Hiraku job ID as the native ID. Attaching a PSI/J job to that ID
reports the saved outcome, or waits for it if execution is still pending. To select
another executor:

```python
import psij

executor = psij.JobExecutor.get_instance(
    "renku", url="https://renkulab.io/p/namespace/project"
)
Job.create(
    command, record_id="baseline", project=project, attributes=attributes
).submit(executor=executor)
```

`Job.load()` and `Job.list()` return new snapshots with saved results; they do
not update existing instances or write completion metadata.
`job.path` identifies the metadata file and is excluded from serialized fields.

MCP provides `job_submit`, `job_list`, and `job_show`. Submission
returns after acceptance; clients inspect saved results with `job_show`.

## Storage and tracking

Each local attempt stores `job.json`, `stdout.log`, and `stderr.log` under
`.hiraku/jobs/<job_id>/`. For `run()`, Hiraku saves the native final status in
`job.json`. For `submit(executor="local-detached")`, Hiraku writes submission metadata to `job.json`;
the launcher alone records its exit code and completion time in `result.json`.
These separate atomic writes cannot overwrite each other. Exclusive creation of
the job directory prevents duplicate execution. An interrupted submission can leave
an attempt without a known outcome; retry by creating another Job.
Nonzero exit codes mean failure; a failed pre/post hook also
fails the execution. Cancellation is not inferred from exit codes.
Exclude `/.hiraku/` from Git. Logs and results remain until you remove them.

Hiraku injects `HIRAKU_RECORD_ID` (empty for unattributed jobs) and `HIRAKU_JOB_ID`.
Scripts log them as CSV
`record_id` / `hiraku_job_id`, or MLflow tags and W&B configuration values
`hiraku.record_id` / `hiraku.job_id`. Both environment variables are reserved;
other environment variables pass through unchanged.

A job can produce several runs or none. Leaderboards read attribution directly
from the tracking source, independently of local job history. `Run.job_id`
optionally links execution details. Logging destinations and code provenance
remain user-managed; scripts outside Hiraku can provide attribution themselves.

## Renku through rnk

Install and authenticate `rnk` independently (`rnk login`), then select a launcher:

```bash
hiraku job submit --executor renku --executor-url https://renkulab.io/p/namespace/project \
  --directory /path/to/remote/project \
  --attribute renku.launcher LAUNCHER_ID \
  -- python -u train.py
```

Alternatively, use the server URL and `--attribute renku.project namespace/project`.
The launcher supplies the image, mounts, and resource allocation. For a Cloud
Native Buildpacks image, add `--attribute renku.command_prefix
/cnb/lifecycle/launcher` to initialize its launch environment. Hiraku overrides
the submitted command without modifying the launcher or uploading local code.

Renku submission records the remote job ID and acceptance-time status only.
Hiraku does not poll for remote completion or create a local result file. Use
`rnk job list`, `rnk job logs`, and `rnk job stop` for status, output, and control.
Each `rnk` submission call allows 20 seconds. PSI/J resource requests, staging,
and launch hooks are unsupported by this adapter. Other executors retain
submission metadata only when using `submit()`;
use their native tools to inspect results.

## Adding executors

Implement PSI/J's `JobExecutor` contract and register a descriptor in an installed
`psij-descriptors/*.py` module, following `src/psij-descriptors/renku.py`.
`src/psij-descriptors/local.py` defines the `local-detached` executor and the
`detached` and `quoted-single` launchers. The detached executor delegates local
execution to PSI/J and publishes saved outcomes through `attach()`.
Foreground execution uses `quoted-single`, a small single-command
launcher with quoted paths to work around PSI/J's unquoted output redirections.
It neither detaches nor records results. Other executors retain their own launchers.
The bundled scripts are `single.sh` for that quoting workaround and `detached.sh`
for detached submission and result recording.
Do not add an `__init__.py` to the descriptors directory.
PSI/J's built-in Slurm executor is also available; supply cluster access and
shared paths through the execution environment.
