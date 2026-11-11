"""Durable local execution, composed with PSI/J's built-in local executor."""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
import weakref
from contextlib import suppress
from datetime import timedelta
from pathlib import Path
from urllib.parse import unquote
from uuid import uuid4

import psutil
from packaging.version import Version
from psij import (
    InvalidJobException,
    Job,
    JobExecutor,
    JobExecutorConfig,
    JobSpec,
    JobState,
    JobStatus,
    SubmitException,
)
from psij.descriptor import Descriptor
from psij.executors.local import LocalJobExecutor as BuiltinLocalExecutor
from psij.serialize import JSONSerializer

from hiraku.io import atomic_write

logger = logging.getLogger(__name__)


class DetachedLocalExecutor(JobExecutor):
    """Own execution state independently of any Hiraku Job or submitting process."""

    def __init__(
        self, url: str | None = None, config: JobExecutorConfig | None = None
    ) -> None:
        super().__init__(url=url, config=config or JobExecutorConfig())
        if url and "://" in url and not url.startswith("file://"):
            raise ValueError("Local executor URL must be a directory or file URI")
        self.root = (
            (
                Path(unquote(url.removeprefix("file://")))
                if url
                else self.config.work_directory / "detached"
            )
            .expanduser()
            .resolve()
        )

    def _directory(self, native_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", native_id):
            raise InvalidJobException("Invalid durable local job ID")
        return self.root / native_id

    def _status(self, native_id: str) -> JobStatus:
        try:
            data = json.loads(
                (self._directory(native_id) / "status.json").read_text(encoding="utf-8")
            )
            state = JobState[data["state"]]
            if not state.final:
                if data["host"] != socket.gethostname():
                    raise ValueError("Local job belongs to another host")
                if time.time() - data["updated_at"] > 30:
                    raise ValueError("Local worker lost; outcome unknown")
            return JobStatus(
                state,
                time=float(data["time"]),
                exit_code=data["exit_code"],
                message=data["message"],
                metadata=dict(data["metadata"]),
            )
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise SubmitException(
                f"Cannot observe local job {native_id}: {exc}"
            ) from exc

    def submit(self, job: Job) -> None:
        spec = self._check_job(job)
        native_id = uuid4().hex
        directory = self._directory(native_id)
        directory.mkdir(parents=True, mode=0o700)
        # Spec/environment travel through stdin, never an execution-state file.
        payload = {
            "spec": JSONSerializer().dumps(spec),
            "duration": spec.attributes.duration.total_seconds()
            if spec.attributes
            else None,
        }
        try:
            runner = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import importlib,sys; "
                    "importlib.import_module('psij-descriptors.detached').DetachedLocalExecutor._work(sys.argv[1])",
                    str(directory),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                start_new_session=True,
            )
            assert runner.stdin
            with runner.stdin:
                runner.stdin.write(json.dumps(payload))
            deadline = time.monotonic() + 30
            while True:
                exited = runner.poll() is not None
                if (directory / "status.json").exists():
                    status = self._status(native_id)
                    if not status.metadata.get("pid"):
                        raise SubmitException(
                            status.message or "Local submission failed"
                        )
                    job._native_id = native_id
                    self._set_job_status(job, status)
                    self._monitor(job, runner)
                    return
                if exited:
                    raise SubmitException(
                        "Local worker exited before acknowledging submission"
                    )
                if time.monotonic() >= deadline:
                    raise SubmitException("Local worker startup timed out")
                time.sleep(0.05)
        except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
            (directory / "cancel").touch()
            raise SubmitException(f"Could not submit local job: {exc}") from exc
        except BaseException:
            (directory / "cancel").touch()
            raise

    def attach(self, job: Job, native_id: str) -> None:
        if job.executor is not None or job.status.state != JobState.NEW:
            raise InvalidJobException("Attach requires an unsubmitted PSI/J Job")
        status = self._status(native_id)
        job.executor = self
        job._native_id = native_id
        self._set_job_status(job, status)
        self._monitor(job)

    def cancel(self, job: Job) -> None:
        native_id = str(job.native_id)
        status = self._status(native_id)
        if not status.final:
            (self._directory(native_id) / "cancel").touch()
            deadline = time.monotonic() + 10
            while not status.final:
                if time.monotonic() >= deadline:
                    raise SubmitException("Local cancellation timed out")
                time.sleep(0.05)
                status = self._status(native_id)
        self._set_job_status(job, status)

    def list(self) -> list[str]:
        return sorted(
            path.parent.name
            for path in self.root.glob("*/status.json")
            if re.fullmatch(r"[a-f0-9]{32}", path.parent.name)
        )

    def _monitor(self, job: Job, runner: subprocess.Popen | None = None) -> None:
        reference = weakref.ref(job)

        def poll():
            while (current := reference()) is not None and not current.status.final:
                try:
                    self.refresh(current)
                except SubmitException as exc:
                    logger.warning("%s", exc)
                del current
                time.sleep(0.2)
            if runner is not None:
                runner.wait()

        threading.Thread(target=poll, name="durable-local-status", daemon=True).start()

    def refresh(self, job: Job) -> None:
        """Publish one current durable observation without another polling thread."""
        self._set_job_status(job, self._status(str(job.native_id)))

    @staticmethod
    def _stop(native: Job) -> None:
        try:
            native.cancel()
        except KeyError:
            # The built-in reaper may have completed the job just before cancellation.
            if not native.status.final:
                raise
        # Let PSI/J reap its own child; psutil.wait() can steal its exit status.
        with suppress(psutil.NoSuchProcess):
            process = psutil.Process(int(native.native_id))
            deadline = time.monotonic() + 10
            while process.is_running():
                if time.monotonic() >= deadline:
                    raise SubmitException("Local cancellation timed out")
                time.sleep(0.05)

    @staticmethod
    def _work(path: str) -> None:
        directory = Path(path)
        native = Job()
        started_at = None

        def record_start(_job, status):
            nonlocal started_at
            if status.state == JobState.ACTIVE:
                started_at = status.time

        def publish(status):
            now = time.time()
            atomic_write(
                directory / "status.json",
                json.dumps(
                    {
                        "state": status.state.name,
                        "time": status.time,
                        "exit_code": status.exit_code,
                        "message": status.message,
                        "host": socket.gethostname(),
                        "updated_at": now,
                        "metadata": {
                            "pid": native.native_id,
                            "runner_pid": os.getpid(),
                            "started_at": started_at,
                            "observed_at": now,
                        },
                    }
                ),
            )

        try:
            payload = json.load(sys.stdin)
            spec: JobSpec = JSONSerializer().loads(payload["spec"])
            if spec.attributes and payload["duration"] is not None:
                # PSI/J currently misparses its own decimal-second duration format.
                spec.attributes.duration = timedelta(seconds=payload["duration"])
            native.spec = spec
            native.set_job_status_callback(record_start)
            BuiltinLocalExecutor(
                config=JobExecutorConfig(work_directory=directory.parent / "launchers")
            ).submit(native)
            while True:
                if (directory / "cancel").exists() and not native.status.final:
                    DetachedLocalExecutor._stop(native)
                publish(native.status)
                if native.status.final:
                    return
                native.wait(timeout=timedelta(seconds=0.2))
        except Exception as exc:
            if native.executor is not None and not native.status.final:
                DetachedLocalExecutor._stop(native)
            publish(JobStatus(JobState.FAILED, message=str(exc)))


__PSI_J_EXECUTORS__ = [
    Descriptor(
        name="detached",
        version=Version("0.1.0"),
        cls="psij-descriptors.detached.DetachedLocalExecutor",
    )
]
