"""Which nodes have a launch in flight: the source of the `running` state.

`hiraku launch` hands the command to a dispatcher and returns when the dispatcher does,
so a launch that takes hours leaves nothing in the record to say it is still going. Two
independent signals fill that gap, and a node is `running` if either holds.

**1. A run that says it is running (no setup).** The tracking backends already carry the
run's own status: MLflow `raw["status"]` (`RUNNING`), Weights & Biases `raw["state"]`
(`running`), and for a CSV source any `status` (or `state`) column. A run attributed to
the node whose status is one of `RUNNING_STATUSES` makes the node `running`, whatever
launched it (Slurm, a laptop, a cloud job). Two limits: the experiment has to register
its run when it *starts* (MLflow and W&B do; a CSV source needs a row written with
`status=running` and updated at the end), and a killed process can leave a run saying
`running` forever, so a run older than `MAX_RUN_AGE` is ignored.

**2. A marker you set when the run cannot report itself.**
`hiraku-agent mark-running ID` with one of `--job-id N` (Slurm), `--pid N` (a process
on this host) or `--check CMD`
(any command that exits 0 while the work is alive, e.g. `docker inspect`)
writes `<git dir>/hiraku-launches/ID.json`. It is never removed by whoever wrote it: it
is read as running for as long as its check holds, so a job that ends, fails or is
cancelled stops counting by itself and no marker can get a node stuck. `mark-done ID`
removes it explicitly.

**Where the marker lives.** Inside the Git directory, never in the working tree: the
dispatchers record `git status --porcelain` as each run's clean/dirty state, so a file
in the tree would make every launch look dirty. The Git directory is also on the same
shared filesystem for every host that sees the repository. Falls back to
`<hiraku_dir>/.launches` outside a repository.

**When it cannot tell.** If `squeue` (or the `--check` command) is missing, times out or
fails for another reason, the work counts as still running rather than finished, so a
node is never released early on a guess. `mark-done` (or deleting the file) clears it.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from hiraku.config import HirakuConfig

from .errors import AgentError

# Slurm job states that mean the job has not finished (`squeue -o %T`).
LIVE_JOB_STATES = frozenset(
    {
        "PENDING",
        "RUNNING",
        "CONFIGURING",
        "COMPLETING",
        "SUSPENDED",
        "REQUEUED",
        "REQUEUE_HOLD",
        "REQUEUE_FED",
        "RESIZING",
        "SIGNALING",
        "STAGE_OUT",
    }
)
# What a run's own status field says while it is not finished (compared lowercased):
# MLflow `RUNNING`/`SCHEDULED`, W&B `running`/`pending`, or a CSV `status` column.
RUNNING_STATUSES = frozenset({"running", "scheduled", "pending"})
# A run that has said "running" for longer than this is taken to be a dead process.
MAX_RUN_AGE = timedelta(days=7)
_JOB_ID = re.compile(r"^\d+(_\d+)?$")
_SQUEUE_TIMEOUT = 20


def launch_dir(config: HirakuConfig) -> Path:
    try:
        result = subprocess.run(
            ["git", "-C", str(config.root), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        result = None
    if result is not None and result.returncode == 0 and result.stdout.strip():
        return (config.root / result.stdout.strip()).resolve() / "hiraku-launches"
    return config.hiraku_dir / ".launches"


def _marker_path(record_id: str, config: HirakuConfig) -> Path:
    return launch_dir(config) / f"{record_id}.json"


def normalize_job_id(raw: str) -> str:
    """`sbatch --parsable` prints `ID` or `ID;cluster`; keep the id, reject the rest."""
    job = str(raw).strip().split(";")[0]
    if not _JOB_ID.match(job):
        raise AgentError(
            f"{raw!r} is not a Slurm job id (expected digits, e.g. the output of "
            f"`sbatch --parsable`)."
        )
    return job


def _job_is_live(job: str) -> bool:
    """Whether Slurm still has `job` unfinished. Unknown counts as live."""
    try:
        result = subprocess.run(
            ["squeue", "-h", "-j", job, "-o", "%T"],
            capture_output=True,
            text=True,
            timeout=_SQUEUE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    if result.returncode != 0:
        # An id Slurm has already forgotten is an error, not an empty answer.
        return "invalid job id" not in result.stderr.lower()
    states = {
        line.strip().upper() for line in result.stdout.splitlines() if line.strip()
    }
    return bool(states & LIVE_JOB_STATES)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _check_is_live(argv: Sequence[str]) -> bool:
    """Whether a liveness command still exits 0. Unknown counts as live."""
    try:
        result = subprocess.run(
            list(argv),
            capture_output=True,
            timeout=_SQUEUE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    return result.returncode == 0


def marker_is_live(marker: Mapping[str, Any]) -> bool:
    """Live if any signal the marker carries says so (jobs, check command, process)."""
    jobs = marker.get("jobs")
    if isinstance(jobs, list) and jobs and any(_job_is_live(str(j)) for j in jobs):
        return True
    check = marker.get("check")
    if isinstance(check, list) and check and _check_is_live([str(c) for c in check]):
        return True
    pid = marker.get("pid")
    if isinstance(pid, int):
        # A process id only means something on the host that wrote it.
        return marker.get("host") != socket.gethostname() or _pid_alive(pid)
    return False


def run_status(run: Any) -> str | None:
    """A run's own status as its tracking backend reports it, lowercased."""
    raw = getattr(run, "raw", None)
    if not isinstance(raw, Mapping):
        return None
    for key in ("status", "state"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return None


def run_in_flight(
    run: Any, *, now: datetime | None = None, max_age: timedelta = MAX_RUN_AGE
) -> bool:
    """Whether `run` says it is still running (and is not too old to believe it)."""
    if run_status(run) not in RUNNING_STATUSES:
        return False
    started = getattr(run, "timestamp", None)
    if isinstance(started, datetime):
        reference = now or datetime.now(UTC)
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        if reference - started > max_age:
            return False
    return True


def active_launches(config: HirakuConfig) -> dict[str, dict[str, Any]]:
    """Every launch still in flight, by node id."""
    directory = launch_dir(config)
    found: dict[str, dict[str, Any]] = {}
    if not directory.is_dir():
        return found
    for path in sorted(directory.glob("*.json")):
        try:
            marker = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(marker, dict) and marker_is_live(marker):
            found[path.stem] = marker
    return found


def running_nodes(
    runs_by_id: Mapping[str, Iterable[Any]], config: HirakuConfig
) -> dict[str, dict[str, Any]]:
    """Every node that is running, by id, from either signal.

    `runs_by_id` is each node's own runs (already fetched by the caller). The value says
    where the answer came from: `source` is `"tracking"` or `"marker"`.
    """
    found: dict[str, dict[str, Any]] = {}
    for node_id, runs in runs_by_id.items():
        for run in runs:
            if run_in_flight(run):
                started = getattr(run, "timestamp", None)
                found[node_id] = {
                    "source": "tracking",
                    "run": getattr(run, "id", None),
                    "status": run_status(run),
                    "started_at": started.isoformat() if started else None,
                }
                break
    for node_id, marker in active_launches(config).items():
        found.setdefault(node_id, {**marker, "source": "marker"})
    return found


def mark_running(
    record_id: str,
    *,
    jobs: Iterable[str] = (),
    pid: int | None = None,
    check: Sequence[str] = (),
    config: HirakuConfig,
) -> dict[str, Any]:
    """Record that `record_id` has jobs in flight, adding to a live marker.

    A marker whose jobs have all ended is replaced, so an earlier attempt's job ids
    never carry over into a new one.
    """
    job_ids = [normalize_job_id(job) for job in jobs]
    if not job_ids and pid is None and not check:
        raise AgentError("Give --job-id (Slurm), --pid (a local process) or --check.")
    path = _marker_path(record_id, config)
    marker: dict[str, Any] = {}
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = None
    if isinstance(previous, dict) and marker_is_live(previous):
        marker = previous
    merged = [*(marker.get("jobs") or []), *job_ids]
    marker.update(
        {
            "record_id": record_id,
            "jobs": list(dict.fromkeys(merged)),
            "host": socket.gethostname(),
            "started_at": marker.get("started_at")
            or datetime.now(UTC).isoformat(timespec="seconds"),
        }
    )
    if pid is not None:
        marker["pid"] = pid
    if check:
        marker["check"] = [str(part) for part in check]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(marker, indent=2), encoding="utf-8")
    return marker


def mark_done(record_id: str, *, config: HirakuConfig) -> bool:
    """Remove `record_id`'s marker. Returns whether there was one."""
    path = _marker_path(record_id, config)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True
