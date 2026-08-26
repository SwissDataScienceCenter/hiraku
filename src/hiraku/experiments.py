from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from hiraku.config import Project
from hiraku.errors import HirakuError
from hiraku.git import create_branch, resolve_commit, switch_branch
from hiraku.records import (
    ExperimentRecord,
    default_experiment,
    dump_frontmatter,
    experiment_paths,
    read_experiment,
    validate_experiment,
    write_experiment,
)
from hiraku.util import utc_now, validate_id


def create_experiment(
    project: Project,
    task_id: str,
    experiment_id: str,
    fmt: str,
    updates: dict[str, Any],
) -> ExperimentRecord:
    validate_id(experiment_id, "experiment")
    directory = project.task_dir(task_id) / "experiments"
    if not (project.task_dir(task_id) / "task.md").is_file():
        raise HirakuError(f"Task {task_id!r} does not exist.")
    directory.mkdir(parents=True, exist_ok=True)
    if any(path.stem == experiment_id for path in experiment_paths(project, task_id)):
        raise HirakuError(f"Experiment {task_id}:{experiment_id} already exists.")
    suffix = ".md" if fmt == "markdown" else ".yml"
    path = directory / f"{experiment_id}{suffix}"
    data = default_experiment(experiment_id, task_id)
    if updates.get("commit") is not None:
        updates["commit"] = (
            resolve_commit(project.root, updates["commit"]) if updates["commit"] else ""
        )
    data.update({key: value for key, value in updates.items() if value is not None})
    data = validate_experiment(data, path, task_id)
    record = ExperimentRecord(data, "", path, fmt)
    if fmt == "markdown":
        path.write_text(dump_frontmatter(data, ""), encoding="utf-8")
    else:
        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return record


def set_experiment(
    project: Project,
    task_id: str,
    experiment_id: str,
    updates: dict[str, Any],
) -> ExperimentRecord:
    record = read_experiment(project, task_id, experiment_id)
    if updates.get("commit") is not None:
        updates["commit"] = (
            resolve_commit(project.root, updates["commit"]) if updates["commit"] else ""
        )
    for key, value in updates.items():
        if value is not None:
            record.data[key] = value
    record.data["updated_at"] = utc_now()
    write_experiment(record)
    return record


def delete_experiment(project: Project, task_id: str, experiment_id: str) -> Path:
    record = read_experiment(project, task_id, experiment_id)
    record.path.unlink()
    return record.path


def create_experiment_branch(
    project: Project, task_id: str, experiment_id: str, from_ref: str | None
) -> tuple[str, str, bool, str | None]:
    branch = f"hiraku/experiment/{task_id}/{experiment_id}"
    requested_source = resolve_commit(project.root, from_ref or f"hiraku/task/{task_id}")
    created = create_branch(project.root, branch, requested_source)
    source = requested_source if created else resolve_commit(project.root, branch)
    warning: str | None = None
    try:
        switch_branch(project.root, branch)
    except HirakuError as exc:
        warning = f"{exc} Switch manually with: git switch {branch}"
    return branch, source, created, warning
