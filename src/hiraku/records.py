from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from hiraku.config import Project
from hiraku.errors import HirakuError, ValidationError
from hiraku.git import current_branch
from hiraku.util import parse_invocation, utc_now, validate_id, validate_utc_timestamp

TASK_FIELDS = {"id", "goal", "branch"}
EXPERIMENT_FIELDS = {
    "id",
    "task_id",
    "summary",
    "hypothesis",
    "status",
    "decision",
    "commit",
    "entrypoints",
    "conclusion",
    "description",
    "created_at",
    "updated_at",
}
EXPERIMENT_REQUIRED_ORDER = [
    "id",
    "task_id",
    "summary",
    "hypothesis",
    "status",
    "decision",
    "commit",
    "entrypoints",
    "conclusion",
    "created_at",
    "updated_at",
]
STATUSES = {"", "proposed", "active", "evaluated", "abandoned"}
DECISIONS = {"", "pending", "accepted", "rejected", "inconclusive"}


@dataclass
class TaskRecord:
    id: str
    goal: str
    branch: str
    body: str
    path: Path

    def as_dict(self, verbose: bool = False) -> dict[str, Any]:
        value = {"id": self.id, "goal": self.goal, "branch": self.branch}
        if verbose:
            value["body"] = self.body
        return value


@dataclass
class ExperimentRecord:
    data: dict[str, Any]
    body: str
    path: Path
    format: str

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def task_id(self) -> str:
        return self.data["task_id"]

    def as_dict(self) -> dict[str, Any]:
        value = dict(self.data)
        if self.format == "markdown":
            value["body"] = self.body
        value["format"] = self.format
        return value


def split_frontmatter(text: str, path: Path) -> tuple[dict[str, Any], str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValidationError(f"{path} must start with YAML front matter.")
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            raw = "".join(lines[1:index])
            try:
                data = yaml.safe_load(raw) or {}
            except yaml.YAMLError as exc:
                raise ValidationError(f"Invalid YAML front matter in {path}: {exc}") from exc
            if not isinstance(data, dict):
                raise ValidationError(f"Front matter in {path} must be a mapping.")
            return data, "".join(lines[index + 1 :]).lstrip("\n")
    raise ValidationError(f"Unclosed YAML front matter in {path}.")


def dump_frontmatter(data: dict[str, Any], body: str) -> str:
    yaml_text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).rstrip()
    suffix = f"\n\n{body.rstrip()}\n" if body.strip() else "\n"
    return f"---\n{yaml_text}\n---{suffix}"


def read_task(project: Project, task_id: str) -> TaskRecord:
    validate_id(task_id, "task")
    path = project.task_dir(task_id) / "task.md"
    if not path.is_file():
        raise HirakuError(f"Task {task_id!r} does not exist.")
    data, body = split_frontmatter(path.read_text(encoding="utf-8"), path)
    unknown = set(data) - TASK_FIELDS
    if unknown:
        raise ValidationError(f"Unknown task fields in {path}: {', '.join(sorted(unknown))}.")
    if data.get("id") != task_id:
        raise ValidationError(f"Task ID in {path} must be {task_id!r}.")
    goal = data.get("goal", "")
    branch = data.get("branch", "")
    if not isinstance(goal, str) or not isinstance(branch, str):
        raise ValidationError(f"Task goal and branch in {path} must be strings.")
    return TaskRecord(task_id, goal, branch, body, path)


def task_ids(project: Project) -> list[str]:
    if not project.tasks_dir.is_dir():
        return []
    return sorted(path.name for path in project.tasks_dir.iterdir() if (path / "task.md").is_file())


def infer_task(project: Project) -> str:
    branch = current_branch(project.root)
    prefix = "hiraku/task/"
    if branch and branch.startswith(prefix):
        task_id = branch[len(prefix) :]
        if "/" not in task_id and (project.task_dir(task_id) / "task.md").is_file():
            return task_id
    matches = [
        task_id for task_id in task_ids(project) if read_task(project, task_id).branch == branch
    ]
    if len(matches) == 1:
        return matches[0]
    raise HirakuError("No current Hiraku task; specify a task explicitly.")


def resolve_task(project: Project, explicit: str | None, option: str | None = None) -> str:
    if explicit and option and explicit != option:
        raise ValidationError(f"Conflicting task selectors: {explicit!r} and {option!r}.")
    return explicit or option or infer_task(project)


def split_object(value: str) -> tuple[str | None, str]:
    if ":" not in value:
        return None, value
    task_id, object_id = value.split(":", 1)
    if not task_id or not object_id:
        raise ValidationError(f"Invalid selector {value!r}; expected [task:]object.")
    return task_id, object_id


def experiment_paths(project: Project, task_id: str) -> list[Path]:
    directory = project.task_dir(task_id) / "experiments"
    if not directory.is_dir():
        return []
    paths = sorted([*directory.glob("*.yml"), *directory.glob("*.md")])
    stems: dict[str, Path] = {}
    for path in paths:
        if path.stem in stems:
            raise ValidationError(
                f"Experiment {task_id}:{path.stem} exists as both "
                f"{stems[path.stem].suffix} and {path.suffix}."
            )
        stems[path.stem] = path
    return paths


def validate_experiment(data: dict[str, Any], path: Path, task_id: str) -> dict[str, Any]:
    unknown = set(data) - EXPERIMENT_FIELDS
    if unknown:
        raise ValidationError(f"Unknown experiment fields in {path}: {', '.join(sorted(unknown))}.")
    experiment_id = data.get("id")
    if not isinstance(experiment_id, str):
        raise ValidationError(f"Experiment ID in {path} must be a string.")
    validate_id(experiment_id, "experiment")
    if experiment_id != path.stem:
        raise ValidationError(f"Experiment ID in {path} must match filename {path.stem!r}.")
    if data.get("task_id") != task_id:
        raise ValidationError(f"Experiment task_id in {path} must be {task_id!r}.")
    missing_timestamps = [field for field in ("created_at", "updated_at") if field not in data]
    if missing_timestamps:
        raise ValidationError(
            f"Experiment {path} is missing required fields: {', '.join(missing_timestamps)}."
        )
    normalized = default_experiment(experiment_id, task_id)
    normalized.update(data)
    for field in ("summary", "hypothesis", "status", "decision", "commit", "conclusion"):
        if not isinstance(normalized[field], str):
            raise ValidationError(f"Experiment field {field!r} in {path} must be a string.")
    if normalized.get("description") is not None and not isinstance(normalized["description"], str):
        raise ValidationError(f"Experiment field 'description' in {path} must be a string.")
    if normalized["status"] not in STATUSES:
        raise ValidationError(f"Invalid experiment status {normalized['status']!r} in {path}.")
    if normalized["decision"] not in DECISIONS:
        raise ValidationError(f"Invalid experiment decision {normalized['decision']!r} in {path}.")
    validate_utc_timestamp(normalized["created_at"], f"Experiment created_at in {path}")
    validate_utc_timestamp(normalized["updated_at"], f"Experiment updated_at in {path}")
    if normalized["commit"] and not re.fullmatch(
        r"(?:[0-9a-f]{40}|[0-9a-f]{64})", normalized["commit"]
    ):
        raise ValidationError(f"Experiment commit in {path} must be an exact hexadecimal SHA.")
    entrypoints = normalized["entrypoints"]
    if not isinstance(entrypoints, list) or not all(isinstance(item, str) for item in entrypoints):
        raise ValidationError(f"Experiment entrypoints in {path} must be a list of strings.")
    if len(entrypoints) != len(set(entrypoints)):
        raise ValidationError(f"Experiment entrypoints in {path} must not contain duplicates.")
    for invocation in entrypoints:
        parse_invocation(invocation)
    return {key: normalized[key] for key in EXPERIMENT_REQUIRED_ORDER if key in normalized} | (
        {"description": normalized["description"]} if "description" in normalized else {}
    )


def default_experiment(
    experiment_id: str, task_id: str, timestamp: str | None = None
) -> dict[str, Any]:
    timestamp = timestamp or utc_now()
    return {
        "id": experiment_id,
        "task_id": task_id,
        "summary": "",
        "hypothesis": "",
        "status": "",
        "decision": "",
        "commit": "",
        "entrypoints": [],
        "conclusion": "",
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def read_experiment_path(path: Path, task_id: str) -> ExperimentRecord:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".md":
        data, body = split_frontmatter(text, path)
        fmt = "markdown"
    else:
        try:
            data = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            raise ValidationError(f"Invalid YAML in {path}: {exc}") from exc
        body = ""
        fmt = "yaml"
    if not isinstance(data, dict):
        raise ValidationError(f"Experiment record {path} must contain a mapping.")
    return ExperimentRecord(validate_experiment(data, path, task_id), body, path, fmt)


def read_experiment(project: Project, task_id: str, experiment_id: str) -> ExperimentRecord:
    validate_id(experiment_id, "experiment")
    matches = [path for path in experiment_paths(project, task_id) if path.stem == experiment_id]
    if not matches:
        raise HirakuError(f"Experiment {task_id}:{experiment_id} does not exist.")
    return read_experiment_path(matches[0], task_id)


def list_experiments(project: Project, task_id: str) -> list[ExperimentRecord]:
    read_task(project, task_id)
    return [read_experiment_path(path, task_id) for path in experiment_paths(project, task_id)]


def write_experiment(record: ExperimentRecord) -> None:
    record.data = validate_experiment(record.data, record.path, record.task_id)
    if record.format == "markdown":
        record.path.write_text(dump_frontmatter(record.data, record.body), encoding="utf-8")
    else:
        record.path.write_text(
            yaml.safe_dump(record.data, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
