"""Observe which project files a launched entrypoint reads and writes.

Observation is opt-in. ``hiraku launch --observe``, or ``HIRAKU_OBSERVE=1``,
snapshots the project before and after the command: files
created or changed are the step's outputs. Files a Python process opens for
reading, reported by an audit hook, and files named in the command are its
inputs. ``hiraku snakemake run`` turns the latest observation of each step into
Snakemake rules, so the workflow needs no hand-written ``Snakefile``.

Observations live in ``<hiraku_dir>/snakemake/``, which ignores itself in Git,
so launching never dirties the checkout.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from collections.abc import Collection, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .config import HirakuConfig
from .io import atomic_write
from .tracking import CSVBackend

# Directories never holding step files: VCS, environments, caches, and the
# stores of tracking backends, which every step writes to.
_SKIPPED = frozenset(
    {
        *(".git", ".hg", ".svn", ".snakemake", ".venv", "venv", "node_modules"),
        *("__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"),
        *(".ipynb_checkpoints", "mlruns", "mlartifacts", "wandb"),
    }
)
_MAX_FILES = 100_000
# More files than this under one directory are declared as the directory.
_MANY_FILES = 50

_HOOK = """\
import atexit, os, sys

_log = os.environ.get("HIRAKU_OBSERVE_LOG")
_opened = set()


def _audit(event, args):
    if event != "open" or not args or isinstance(args[0], int):
        return
    path, mode, flags = (*args, None, None)[:3]
    writing = any(c in mode for c in "wax+") if isinstance(mode, str) else (
        isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR)
    )
    if not writing:
        try:
            _opened.add(os.path.abspath(os.fsdecode(path)))
        except (TypeError, ValueError):
            pass


def _save():
    if _log and _opened:
        data = "".join(f"{path}\\n" for path in sorted(_opened))
        _opened.clear()
        with open(f"{_log}.{os.getpid()}", "w", encoding="utf-8") as stream:
            stream.write(data)


if _log:
    sys.addaudithook(_audit)
    atexit.register(_save)

# Load the project's own sitecustomize, which this file shadows.
_here = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [item for item in sys.path if os.path.abspath(item or ".") != _here]
import importlib.machinery, importlib.util

_spec = importlib.machinery.PathFinder.find_spec("sitecustomize", sys.path)
if _spec is not None and _spec.loader is not None:
    _spec.loader.exec_module(importlib.util.module_from_spec(_spec))
"""


def observations_dir(config: HirakuConfig) -> Path:
    """Return the Git-ignored directory holding observations and generated files."""
    path = config.hiraku_dir / "snakemake"
    path.mkdir(parents=True, exist_ok=True)
    ignore = path / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")
    return path


def _snapshot(config: HirakuConfig) -> dict[str, tuple[int, int]] | None:
    """Return ``{relative path: (size, mtime_ns)}``, or ``None`` past the limit."""
    root = config.root
    hiraku_dir = config.hiraku_dir.resolve()
    files: dict[str, tuple[int, int]] = {}
    for directory, names, filenames in os.walk(root):
        current = Path(directory)
        names[:] = [
            name
            for name in names
            if name not in _SKIPPED
            and not (current / name).is_symlink()
            and (current / name).resolve() != hiraku_dir
        ]
        for filename in filenames:
            path = current / filename
            try:
                status = path.lstat()
            except OSError:
                continue
            if not path.is_symlink():
                files[path.relative_to(root).as_posix()] = (
                    status.st_size,
                    status.st_mtime_ns,
                )
        if len(files) > _MAX_FILES:
            return None
    return files


def _project_path(config: HirakuConfig, value: str) -> str | None:
    """Return ``value`` as a project-relative POSIX path, if it lies inside."""
    path = Path(value)
    if not path.is_absolute():
        path = config.root / path
    try:
        return path.resolve().relative_to(config.root.resolve()).as_posix()
    except (OSError, ValueError):
        return None


def store_files(config: HirakuConfig, backend: Any) -> set[str]:
    """Return the project files a tracking backend writes on every run."""
    paths = []
    if isinstance(backend, CSVBackend):
        paths.append(str(backend.path or config.root / "runs.csv"))
    uri = getattr(backend, "tracking_uri", None)
    if isinstance(uri, str) and uri.startswith("sqlite:///"):
        paths.append(uri.removeprefix("sqlite:///"))
    return {path for value in paths if (path := _project_path(config, value))}


class Observer:
    """Watch one launch; :meth:`finish` stores what it read and wrote."""

    def __init__(self, config: HirakuConfig, *, backend: Any = None) -> None:
        self.config = config
        self.excluded = store_files(config, backend)
        self.before = _snapshot(config)
        self.scratch = Path(tempfile.mkdtemp(prefix="hiraku-observe-"))
        (self.scratch / "sitecustomize.py").write_text(_HOOK, encoding="utf-8")

    def environment(self, base: Mapping[str, str]) -> dict[str, str]:
        """Return the variables that load the audit hook into Python children."""
        python_path = base.get("PYTHONPATH")
        return {
            "HIRAKU_OBSERVE_LOG": str(self.scratch / "opened"),
            "PYTHONPATH": os.pathsep.join(
                [str(self.scratch), *([python_path] if python_path else [])]
            ),
        }

    def discard(self) -> None:
        """Remove the observer's scratch files without storing anything."""
        shutil.rmtree(self.scratch, ignore_errors=True)

    def finish(
        self,
        *,
        record_id: str,
        entrypoint: str,
        arguments: Sequence[str],
        command: Sequence[str],
        returncode: int,
        options: Sequence[str] = (),
    ) -> Path | None:
        """Store the observation of a successful launch and return its path.

        ``options`` are the launch options to repeat, such as ``--dispatch``.
        """
        try:
            return self._store(
                record_id, entrypoint, arguments, command, returncode, options
            )
        finally:
            self.discard()

    def _store(
        self,
        record_id: str,
        entrypoint: str,
        arguments: Sequence[str],
        command: Sequence[str],
        returncode: int,
        options: Sequence[str],
    ) -> Path | None:
        if returncode or self.before is None:
            return None
        after = _snapshot(self.config)
        if after is None:
            return None
        changed = [
            path
            for path, state in after.items()
            if self.before.get(path) != state and path not in self.excluded
        ]
        opened = [
            line
            for log in self.scratch.glob("opened.*")
            for line in log.read_text(encoding="utf-8").splitlines()
        ]
        named = [
            part
            for token in command
            for part in (token, token.partition("=")[2])
            if part and not part.startswith("-")
        ]
        read = {
            path
            for value in (*opened, *named)
            if (path := _project_path(self.config, value)) in self.before
            and path not in self.excluded
        }
        outputs = _directories(changed, self.before)
        inputs = sorted(
            path for path in _directories(read, {}) if not _covered(path, outputs)
        )
        if not inputs and not outputs:
            return None
        directory = observations_dir(self.config) / "observed"
        directory.mkdir(exist_ok=True)
        destination = directory / f"{record_id}__{entrypoint}.json"
        # A step that skips work when its output exists still owns that output.
        if destination.exists():
            previous = json.loads(destination.read_text(encoding="utf-8"))
            if (previous.get("arguments"), previous.get("options", [])) == (
                list(arguments),
                list(options),
            ):
                kept = [
                    path
                    for path in previous["outputs"]
                    if (self.config.root / path.rstrip("/")).exists()
                ]
                outputs = sorted({*outputs, *kept})
                inputs = [path for path in inputs if not _covered(path, outputs)]
        observation = {
            "record": record_id,
            "entrypoint": entrypoint,
            "options": list(options),
            "arguments": list(arguments),
            "inputs": inputs,
            "outputs": outputs,
            "observed_at": datetime.now(UTC).isoformat(),
        }
        atomic_write(destination, json.dumps(observation, indent=2) + "\n")
        return destination


def _directories(paths: Collection[str], before: Mapping[str, Any]) -> list[str]:
    """Replace many files under one directory by that directory, as ``dir/``.

    Outputs collapse only within directories the launch created, so a step
    writing one file next to existing ones keeps its file. ``before`` is empty
    for inputs, which collapse wherever a directory holds many of them.
    """
    existing = {
        str(parent) for path in before for parent in PurePosixPath(path).parents
    }
    groups: dict[str, list[str]] = {}
    for path in paths:
        parents = [str(item) for item in reversed(PurePosixPath(path).parents)][1:]
        top = next((item for item in parents if item not in existing), None)
        if before and top is None:
            groups.setdefault(path, []).append(path)
            continue
        key = top if before else (parents[-1] if parents else path)
        groups.setdefault(key, []).append(path)
    result = []
    for members in groups.values():
        if len(members) > _MANY_FILES:
            result.append(f"{os.path.commonpath(members)}/")
        else:
            result.extend(members)
    return sorted(result)


def _covered(path: str, outputs: Collection[str]) -> bool:
    return path in outputs or any(
        item.endswith("/") and path.startswith(item) for item in outputs
    )


def start(config: HirakuConfig, *, backend: Any = None) -> Observer | None:
    """Return an observer for a launch, or ``None`` if it cannot be observed.

    Launches made by ``hiraku snakemake run`` are not observed: their files are
    already declared.
    """
    if os.environ.get("HIRAKU_WORKFLOW_RUN_ID"):
        return None
    try:
        observer = Observer(config, backend=backend)
    except OSError:
        return None
    if observer.before is None:
        print(
            f"hiraku: not observing files: more than {_MAX_FILES} in the project",
            file=sys.stderr,
        )
        observer.discard()
        return None
    return observer


def load_observations(config: HirakuConfig) -> list[dict[str, Any]]:
    """Return the latest observation of every launched step."""
    directory = config.hiraku_dir / "snakemake" / "observed"
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"))
    ]
