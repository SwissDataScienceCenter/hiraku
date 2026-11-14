"""Read normalized runs from trusted project Python definitions."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

from ..errors import BackendError
from ..project import Project
from .base import BackendOperations, PythonBackendConfig, Run


def load_definition(path: Path) -> ModuleType:
    """Execute a definition afresh, without bytecode caching or changing sys.path."""
    module = ModuleType(f"_hiraku_leaderboard_{path.stem}")
    module.__file__ = str(path)
    try:
        exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
        if not callable(getattr(module, "load_runs", None)):
            raise ValueError("define a callable load_runs(project)")
    except Exception as exc:
        raise BackendError(f"Cannot load Python leaderboard {path}: {exc}") from exc
    return module


class PythonBackend(BackendOperations):
    """Apply normal Hiraku queries to a project's read-only run provider."""

    def __init__(self, project: Project, *, settings: PythonBackendConfig) -> None:
        self.project = project
        self.path = (project.root / settings.path).resolve()

    def _read_source_runs(self) -> list[Run]:
        module = load_definition(self.path)
        try:
            runs = module.load_runs(self.project)
            if not isinstance(runs, list) or any(
                not isinstance(run, Run) for run in runs
            ):
                raise ValueError("load_runs(project) must return list[Run]")
            if len({run.id for run in runs}) != len(runs):
                raise ValueError("run IDs must be unique within a leaderboard")
            return runs
        except Exception as exc:
            raise BackendError(
                f"Cannot read Python leaderboard {self.path}: {exc}"
            ) from exc
