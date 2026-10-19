"""Local Snakemake execution capture and run source."""

from __future__ import annotations

import json
import re
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from ..config import HirakuConfig
from ..errors import BackendError, ValidationError
from ..io import atomic_write
from ..run import Run, RunFile
from .base import TrackingBackend

_JOB = re.compile(r"^(?:localrule|rule|checkpoint) [^:]+:$")
_FILES = re.compile(r"^    (input|output): (.*)$")


class SnakemakeBackendConfig(BaseModel):
    """Storage location for Snakemake run records."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["snakemake"] = "snakemake"
    path: Path | None = None


class SnakemakeBackend(TrackingBackend):
    """Read one JSON file per Snakemake workflow execution."""

    def __init__(self, config: HirakuConfig, path: Path | None = None) -> None:
        self.config = config
        self.path = (
            (config.root / path).resolve()
            if path is not None and not path.is_absolute()
            else path
        ) or config.hiraku_dir / "runs" / "snakemake"

    def _read_source_runs(self, **options: Any) -> list[Run]:
        if not self.path.exists():
            return []
        if not self.path.is_dir():
            raise BackendError(f"Snakemake run path is not a directory: {self.path}")
        runs = []
        for path in sorted(self.path.glob("*.json")):
            try:
                runs.append(Run.model_validate_json(path.read_text(encoding="utf-8")))
            except (OSError, ValueError) as exc:
                raise BackendError(f"Invalid Snakemake run file {path}: {exc}") from exc
        return runs

    def write(self, run: Run) -> Path:
        """Persist a completed run without replacing an existing run ID."""
        if not re.fullmatch(r"[A-Za-z0-9_-]+", run.id):
            raise ValidationError("Snakemake run ID contains invalid characters")
        self.path.mkdir(parents=True, exist_ok=True)
        path = self.path / f"{run.id}.json"
        if path.exists():
            raise BackendError(f"Snakemake run already exists: {run.id}")
        data = run.model_dump(mode="json", exclude={"metrics", "params", "raw"})
        data.update(
            metrics=dict(run.metrics), params=dict(run.params), raw=dict(run.raw)
        )
        encoded = json.dumps(data, indent=2)
        atomic_write(path, encoded + "\n")
        return path


def declared_files(log: str) -> tuple[set[str], set[str]]:
    """Extract file declarations from Snakemake's human-readable job blocks.

    This format is best effort; dynamic checkpoints and non-local storage may
    omit files. A run therefore stores the actual declaration scope in raw.
    """
    inputs: set[str] = set()
    outputs: set[str] = set()
    in_job = False
    for line in log.splitlines():
        if _JOB.match(line):
            in_job = True
            continue
        if not in_job:
            continue
        match = _FILES.match(line)
        if match:
            values = {item.strip() for item in match[2].split(",") if item.strip()}
            (inputs if match[1] == "input" else outputs).update(values)
        elif line and not line.startswith("    "):
            in_job = False
    return inputs, outputs


def observe(root: Path, paths: set[str]) -> tuple[RunFile, ...]:
    """Observe declared local files without following paths outside the project."""
    found = []
    for name in sorted(paths):
        candidate = root / name
        if not candidate.resolve().is_relative_to(root.resolve()):
            found.append(RunFile(path=name, state="unavailable"))
            continue
        try:
            if candidate.is_file():
                found.append(
                    RunFile(path=name, state="file", size=candidate.stat().st_size)
                )
            elif candidate.is_dir():
                found.append(RunFile(path=name, state="directory"))
            else:
                found.append(RunFile(path=name, state="missing"))
        except OSError:
            found.append(RunFile(path=name, state="unavailable"))
    return tuple(found)


def execute(
    config: HirakuConfig,
    *,
    record_id: str,
    snakefile: Path,
    targets: tuple[str, ...] = (),
    cores: int = 1,
    dry_run: bool = False,
    command: str = "snakemake",
    command_args: tuple[str, ...] = (),
    options: tuple[str, ...] = (),
) -> tuple[Run, int]:
    """Run Snakemake and persist one attributed workflow execution."""
    from ..record import load_record

    if cores < 1:
        raise ValidationError("Snakemake cores must be positive")
    record = load_record(record_id, config=config)
    backend = record.resolve_tracking_backend(config=config)
    if not isinstance(backend, SnakemakeBackend):
        raise ValidationError(
            "Record tracking_backend must be snakemake to capture a workflow run"
        )
    snakefile = snakefile if snakefile.is_absolute() else config.root / snakefile
    if not snakefile.is_file():
        raise ValidationError(f"Snakefile not found: {snakefile}")
    arguments = [
        command, *command_args, "--snakefile", str(snakefile), "--cores", str(cores),
        *options, *targets,
    ]
    started = datetime.now(UTC)
    try:
        preview = subprocess.run(
            [*arguments, "--dry-run", "--forceall"],
            cwd=config.root,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise BackendError(f"Cannot start Snakemake: {exc}") from exc
    inputs, outputs = declared_files(preview.stdout + "\n" + preview.stderr)
    before = observe(config.root, inputs)
    try:
        result = preview if dry_run or preview.returncode else subprocess.run(
            arguments, cwd=config.root, text=True, check=False
        )
    except OSError as exc:
        raise BackendError(f"Cannot start Snakemake: {exc}") from exc
    status = "failed" if result.returncode else "planned" if dry_run else "succeeded"
    run = Run(
        id=f"snake_{uuid.uuid4().hex}",
        record_id=record_id,
        entrypoint="snakemake",
        commit=record.commit,
        timestamp=started,
        status=status,
        declared_inputs=tuple(sorted(inputs)),
        declared_outputs=tuple(sorted(outputs)),
        inputs=before,
        outputs=observe(config.root, outputs),
        params={"targets": list(targets), "cores": cores, "snakefile": str(snakefile)},
        raw={
            "returncode": result.returncode,
            "declaration_source": "snakemake --dry-run --forceall job log",
            "declarations_complete": False,
        },
    )
    backend.write(run)
    return run, result.returncode
