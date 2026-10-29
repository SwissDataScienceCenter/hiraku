# Jobs

A record describes research context. A job is a durable receipt for one command
and its selected source. Tracking remains independent: a job may create zero,
one, or several tracked runs.

## Run and inspect

Running `hiraku run` by itself prints the command help. To intentionally use a
record's default command, specify a record or another run option.

Records do not need a default command. Submit any argv directly; Hiraku
preserves each argument literally and records it in the job receipt:

```bash
job_json=$(hiraku run --record experiment-1 --json -- \
  uv run --locked python analyze.py --seed 7)
job_id=$(printf '%s' "$job_json" | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')
```

For interactive use, the default output is just the job ID, so this is also
safe for command substitution:

```bash
job_id=$(hiraku run --record experiment-1 -- uv run --locked python train.py)
```

An optional inherited default command can make repeated runs shorter. Configure
it in `hiraku.yml` or record front matter:

```yaml
command: [uv, run, --locked, python, train.py, --seed, "42"]
```

The nearest record command replaces the inherited command. Otherwise Hiraku
uses an ancestor command and then the project default.

```bash
job_id=$(hiraku run --record experiment-1)
hiraku job status "$job_id" --json
hiraku job wait "$job_id"
hiraku job logs "$job_id"
hiraku job logs "$job_id" --stream stderr --follow
hiraku job list --record experiment-1 --json
hiraku job cancel "$job_id"
```

The nearest record command replaces the complete inherited argv. An explicit
command after `--` always takes precedence and replaces that default:

```bash
hiraku run --record experiment-1 -- \
  uv run --locked python analyze.py --seed 7
```

`--record` defaults to the active record. Jobs currently execute locally.
The local backend uses a managed source snapshot. To run on another machine,
install Hiraku and the project environment there, then submit the job from that
project directory.

`--dry-run` prints the resolved request without creating a job.

Submission prints a job ID and the detached supervisor continues after the CLI
exits. `wait` returns the command's exit code. `unknown` means the executor
cannot currently confirm the execution; it does not mean the job failed. `wait`
retries transient unknown status observations. Local supervisor failures can
report `orphaned` or `lost`. stdout and stderr are saved separately.

## Source and provenance

Job submission requires a clean Git working tree: staged changes, unstaged
changes, and non-ignored untracked files must be committed or discarded first.
This applies even when `--commit` or a record commit selects an earlier
revision. Hiraku executes the selected commit in an isolated source snapshot;
ignored files, datasets, and installed environments are excluded. The receipt
records the resolved commit and a SHA-256 digest of the captured files. Hiraku
rejects symlinks that escape the repository. Outputs and logs stay with the job
after completion.

Source selection belongs to Hiraku core; the local compute backend owns
execution lifecycle. Jobs use managed snapshots of the source tree.

The optional dispatcher sets up the runtime and then invokes the command from
the local snapshot. Without a dispatcher, Hiraku runs the command directly.
Configure environment setup with a dispatcher or command such as
`uv run --locked`; Hiraku does not install dependencies or transfer data.

The job store is `hiraku/.jobs/` under the configured state directory. It holds
the versioned receipt, source snapshots, and job logs.
Environment additions are included in the immutable `JobSpec`; do not pass
secrets as environment additions or command arguments.

Jobs receive `HIRAKU_JOB_ID`, `HIRAKU_JOB_DIR`, `HIRAKU_RECORD_ID`,
`HIRAKU_RECORD_FILE` (the submitted record snapshot), `HIRAKU_ROOT`,
`HIRAKU_TRACKING_ROOT`, `HIRAKU_COMMIT`, `HIRAKU_GIT_STATE`, and
`HIRAKU_SOURCE_DIGEST`. These paths refer to the execution machine. Log the job
ID as `job_id` in CSV or `hiraku.job_id`
in MLflow/W&B metadata. Queries over tracked runs remain independent of job state.
`hiraku job wait` retries transient `unknown` observations and raises a
monitoring error if the backend remains unobservable for 30 seconds; its
`--timeout` option separately limits the overall wait.

## Python API and migration

```python
from hiraku import JobSpec, job_logs, submit_job, wait_job

job = submit_job(["python", "analyze.py"], record_id="experiment-1")
completed = wait_job(job.id)
print(completed.status)
print("".join(job_logs(job.id)))
```

Replace `entrypoints:` configuration with a default `command:` argv list. Move
former default arguments directly into that list and provide alternative
commands to `hiraku run`. `Entrypoint`, `Record.entrypoint`,
`hiraku record entrypoints`, `hiraku launch`, and `hiraku job submit` are
removed.

Hiraku keeps execution receipts separate from tracking, following the durable
job and per-job staging ideas in [Claude Science](https://claude.com/docs/claude-science/remote-compute-clusters)
and the compute/tracking separation in [OpenResearch](https://github.com/alphaXiv/OpenResearch).
SSH execution, remote source staging, synchronization, environment management,
and resume are future work.
