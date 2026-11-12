"""Local PSI/J execution with launcher-recorded outcomes."""

import os
import sys
from copy import copy
from pathlib import Path

from packaging.version import Version
from psij import (
    InvalidJobException,
    Job,
    JobAttributes,
    JobExecutorConfig,
    JobState,
    JobStatus,
)
from psij.descriptor import Descriptor
from psij.executors.local import LocalJobExecutor
from psij.launchers.script_based_launcher import ScriptBasedLauncher
from pydantic import AwareDatetime, BaseModel, Field


class _Result(BaseModel):
    exit_code: int = Field(strict=True, ge=0, le=255)
    finished_at: AwareDatetime


class RecordingLocalExecutor(LocalJobExecutor):
    """Detach local commands and record their outcomes independently of the caller."""

    def submit(self, job: Job) -> None:
        if os.name != "posix":
            raise InvalidJobException("Local recording requires a POSIX system")
        spec = job.spec
        if spec.launcher not in (None, "single", "hiraku-single"):
            raise InvalidJobException("Local recording requires the single launcher")
        if spec.stdout_path is None:
            raise InvalidJobException("Local recording requires stdout_path")
        spec.launcher = "hiraku-record"
        spec.stdin_path = spec.stdin_path or Path(os.devnull)
        spec.attributes = copy(spec.attributes) or JobAttributes()
        spec.attributes.custom_attributes = {
            **(spec.attributes.custom_attributes or {}),
            "hiraku.result_path": str(Path(spec.stdout_path).with_name("result.json")),
        }
        super().submit(job)

    @staticmethod
    def read_result(path: Path) -> JobStatus | None:
        try:
            result = _Result.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        return JobStatus(
            JobState.COMPLETED if result.exit_code == 0 else JobState.FAILED,
            time=result.finished_at.timestamp(),
            exit_code=result.exit_code,
        )


class SingleLauncher(ScriptBasedLauncher):
    """PSI/J's single launcher with quoted paths, including output redirections."""

    def __init__(self, config: JobExecutorConfig | None = None):
        super().__init__(
            Path(__file__).parent.parent / "hiraku/resources/launchers" / self.script,
            config,
        )

    script = "single.sh"


class RecordingLauncher(SingleLauncher):
    script = "detach.sh"

    def get_additional_args(self, job: Job) -> list[str]:
        attributes = job.spec.attributes.custom_attributes or {}
        if "hiraku.result_path" not in attributes:
            raise InvalidJobException("Recording requires hiraku.result_path")
        if job.spec.resources and job.spec.resources.computed_process_count != 1:
            raise InvalidJobException("Recording supports a single local process")
        return [str(attributes["hiraku.result_path"])]

    def get_launch_command(self, job: Job) -> list[str]:
        return [
            sys.executable,
            "-c",
            "import os,sys; os.setsid(); os.execv(sys.argv[1], sys.argv[1:])",
            *super().get_launch_command(job),
        ]

    def is_launcher_failure(self, output: str) -> bool:
        # The detached launcher disconnects diagnostics from the submitter.
        return (
            bool(output.strip()) and "_PSI_J_LAUNCHER_DONE" not in output.splitlines()
        )


__PSI_J_LAUNCHERS__ = [
    Descriptor(
        name="hiraku-single",
        version=Version("0.1.0"),
        cls="psij-descriptors.recording.SingleLauncher",
    ),
    Descriptor(
        name="hiraku-record",
        version=Version("0.1.0"),
        cls="psij-descriptors.recording.RecordingLauncher",
    ),
]

__PSI_J_EXECUTORS__ = [
    Descriptor(
        name="hiraku-local",
        version=Version("0.1.0"),
        cls="psij-descriptors.recording.RecordingLocalExecutor",
    )
]
