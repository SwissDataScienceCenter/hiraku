"""Project-local SQLite checkpoint storage for LangGraph workflows."""

from __future__ import annotations

from pathlib import Path

from ..project import Project

CHECKPOINTS = "langgraph-checkpoints.sqlite"


def checkpoint_database(project: Project) -> Path:
    """Return the project-root checkpoint database path."""
    return project.root / ".hiraku" / CHECKPOINTS


def prepare_checkpoint_storage(project: Project) -> Path:
    """Ensure the ignored project-root checkpoint directory exists."""
    directory = project.root / ".hiraku"
    directory.mkdir(parents=True, exist_ok=True)
    ignore = directory / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")
    return checkpoint_database(project)
