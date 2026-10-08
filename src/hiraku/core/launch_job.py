"""Persistent background jobs for MCP experiment launches."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import HirakuConfig
from .errors import LaunchError, ValidationError
from .git import GitRepository
from .io import atomic_write

_POLL_WAIT_SECONDS = 10
_LOG_TAIL_BYTES = 12_000


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"Cannot read launch state at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"Launch state at {path} must be a JSON object")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    atomic_write(
        path,
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        mode=0o600,
    )


def _launch_root(config: HirakuConfig) -> Path:
    git = GitRepository(config)
    value = git._git("rev-parse", "--git-path", "hiraku/launches").stdout.strip()
    path = Path(value)
    if not path.is_absolute():
        path = git.root / path
    return path.resolve()


def submit_launch(
    config: HirakuConfig,
    *,
    record_id: str,
    entrypoint: str,
    commit: str,
    args: dict[str, Any],
    cpu_time_limit_seconds: int | None,
) -> dict[str, Any]:
    """Start a detached launch worker and return its durable handle."""
    launch_id = str(uuid.uuid4())
    job_dir = _launch_root(config) / launch_id
    job_dir.mkdir(parents=True, mode=0o700)
    state_path = job_dir / "state.json"
    request_path = job_dir / "request.json"
    log_path = job_dir / "output.log"
    state: dict[str, Any] = {
        "launch_id": launch_id,
        "record_id": record_id,
        "entrypoint": entrypoint,
        "commit": commit,
        "status": "queued",
        "created_at": _now(),
        "started_at": None,
        "finished_at": None,
        "pid": None,
        "returncode": None,
        "error": None,
    }
    request = {
        "config_path": str(config.path),
        "record_id": record_id,
        "entrypoint": entrypoint,
        "commit": commit,
        "args": args,
        "cpu_time_limit_seconds": cpu_time_limit_seconds,
    }
    _write_json(state_path, state)
    _write_json(request_path, request)
    log_path.touch(mode=0o600)
    command = [sys.executable, "-m", "hiraku.core.launch_job", str(job_dir)]
    options: dict[str, Any] = {}
    if os.name == "posix":
        options["start_new_session"] = True
    elif hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        with log_path.open("ab") as output:
            process = subprocess.Popen(
                command,
                cwd=config.root,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                close_fds=True,
                **options,
            )
    except OSError as exc:
        state["status"] = "failed"
        state["finished_at"] = _now()
        state["error"] = f"Could not start launch worker: {exc}"
        _write_json(state_path, state)
        request_path.unlink(missing_ok=True)
        raise LaunchError(state["error"]) from exc
    state["pid"] = process.pid
    _write_json(state_path, state)
    return _public_state(state, log_path)


def _public_state(state: dict[str, Any], log_path: Path) -> dict[str, Any]:
    result = {
        key: state.get(key)
        for key in (
            "launch_id",
            "record_id",
            "entrypoint",
            "commit",
            "status",
            "created_at",
            "started_at",
            "finished_at",
            "pid",
            "returncode",
            "error",
        )
    }
    result["log_tail"] = _tail(log_path)
    return result


def _tail(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - _LOG_TAIL_BYTES))
            content = stream.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    return content[-_LOG_TAIL_BYTES:]


def get_launch_status(config: HirakuConfig, launch_id: str) -> dict[str, Any]:
    """Read launch state and reconcile a worker that exited unexpectedly."""
    try:
        canonical_id = str(uuid.UUID(launch_id))
    except (ValueError, AttributeError) as exc:
        raise ValidationError(f"Invalid launch ID: {launch_id!r}") from exc
    job_dir = _launch_root(config) / canonical_id
    state_path = job_dir / "state.json"
    state = _read_json(state_path)
    if state.get("launch_id") != canonical_id:
        raise ValidationError("Launch state ID does not match its directory")
    if state.get("status") in {"queued", "running"}:
        pid = state.get("pid")
        if isinstance(pid, int) and not _process_exists(pid):
            state["status"] = "interrupted"
            state["finished_at"] = _now()
            state["error"] = "Launch worker exited without recording a terminal status."
            _write_json(state_path, state)
            (job_dir / "request.json").unlink(missing_ok=True)
    return _public_state(state, job_dir / "output.log")


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def run_worker(job_dir: Path) -> int:
    """Execute one persisted launch request and record its final state."""
    state_path = job_dir / "state.json"
    request_path = job_dir / "request.json"
    deadline = time.monotonic() + _POLL_WAIT_SECONDS
    while time.monotonic() < deadline:
        state = _read_json(state_path)
        if state.get("pid") == os.getpid():
            break
        time.sleep(0.05)
    else:
        return 2

    state["status"] = "running"
    state["started_at"] = _now()
    _write_json(state_path, state)
    try:
        request = _read_json(request_path)
        config = HirakuConfig.load(request["config_path"])
        from .launch import launch
        from .record import load_record

        record = load_record(request["record_id"], config=config)
        if record.commit != request["commit"]:
            raise ValidationError("Experiment commit changed before launch worker ran")
        git = GitRepository(config)
        if git.resolve(record.commit) != record.commit:
            raise ValidationError("Experiment commit is invalid")
        definition = record.entrypoint(request["entrypoint"], config=config)
        result = launch(
            definition,
            name=request["entrypoint"],
            args=request["args"],
            record_id=record.id,
            commit=record.commit,
            capture_output=False,
            check=False,
            cpu_time_limit_seconds=request["cpu_time_limit_seconds"],
            config=config,
        )
        state["returncode"] = result.returncode
        state["status"] = "completed" if result.returncode == 0 else "failed"
        if result.returncode:
            state["error"] = (
                f"Experiment process exited with status {result.returncode}."
            )
    except Exception as exc:
        state["status"] = "failed"
        state["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        state["finished_at"] = _now()
        _write_json(state_path, state)
        request_path.unlink(missing_ok=True)
    return 0 if state["status"] == "completed" else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m hiraku.core.launch_job JOB_DIRECTORY")
    raise SystemExit(run_worker(Path(sys.argv[1])))
