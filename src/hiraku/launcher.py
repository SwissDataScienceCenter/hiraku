from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from hiraku.config import Project
from hiraku.errors import HirakuError, ValidationError
from hiraku.git import resolve_commit, run_git
from hiraku.records import branch_identity, read_experiment, read_task, resolve_task, split_object
from hiraku.util import parse_invocation, safe_relative, short_commit


@dataclass(frozen=True)
class Entrypoint:
    name: str
    command: list[str]
    args: dict[str, Any]
    positionals: list[str]
    cwd: str
    env: dict[str, str]


def add_launch_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    command = commands.add_parser("launch", help="Launch a named task entrypoint")
    command.add_argument("--commit")
    command.add_argument("--experiment")
    command.add_argument("entrypoint")
    command.add_argument("arguments", nargs=argparse.REMAINDER)


def load_launch(source_root: Path, project: Project, task_id: str) -> dict[str, Entrypoint]:
    relative_task = project.task_dir(task_id).relative_to(project.root)
    path = source_root / relative_task / "launch.yml"
    if not path.is_file():
        raise HirakuError(f"No launch.yml for task {task_id!r} in selected source.")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValidationError(f"Invalid launch configuration {path}: {exc}") from exc
    if not isinstance(data, dict) or set(data) != {"entrypoints"}:
        raise ValidationError(f"{path} must contain only an 'entrypoints' mapping.")
    raw_entrypoints = data["entrypoints"]
    if not isinstance(raw_entrypoints, dict):
        raise ValidationError(f"'entrypoints' in {path} must be a mapping.")
    result: dict[str, Entrypoint] = {}
    for name, raw in raw_entrypoints.items():
        if not isinstance(name, str) or not name:
            raise ValidationError(f"Entrypoint names in {path} must be non-empty strings.")
        if not isinstance(raw, dict):
            raise ValidationError(f"Entrypoint {name!r} in {path} must be a mapping.")
        unknown = set(raw) - {"command", "args", "positionals", "cwd", "env"}
        if unknown:
            raise ValidationError(
                f"Unknown fields for entrypoint {name!r}: {', '.join(sorted(unknown))}."
            )
        argv = raw.get("command")
        if not isinstance(argv, list) or not argv or not all(isinstance(v, str) for v in argv):
            raise ValidationError(f"Entrypoint {name!r} command must be a non-empty string list.")
        arguments = raw.get("args", {})
        if not isinstance(arguments, dict) or not all(isinstance(k, str) for k in arguments):
            raise ValidationError(f"Entrypoint {name!r} args must be a string-keyed mapping.")
        positionals = raw.get("positionals", [])
        if not isinstance(positionals, list) or not all(
            isinstance(value, (str, int, float)) and not isinstance(value, bool)
            for value in positionals
        ):
            raise ValidationError(f"Entrypoint {name!r} positionals must be a scalar list.")
        cwd = raw.get("cwd", ".")
        if not isinstance(cwd, str):
            raise ValidationError(f"Entrypoint {name!r} cwd must be a string.")
        safe_relative(source_root, cwd, f"Entrypoint {name!r} cwd")
        environment = raw.get("env", {})
        if not isinstance(environment, dict) or not all(
            isinstance(key, str) and isinstance(value, (str, int, float, bool))
            for key, value in environment.items()
        ):
            raise ValidationError(f"Entrypoint {name!r} env must be a scalar mapping.")
        result[name] = Entrypoint(
            name=name,
            command=list(argv),
            args=arguments,
            positionals=[str(value) for value in positionals],
            cwd=cwd,
            env={key: str(value) for key, value in environment.items()},
        )
    return result


def render_named_args(arguments: dict[str, Any], entrypoint: str) -> list[str]:
    result: list[str] = []
    for key, value in arguments.items():
        if not key or key.startswith("-") or any(character.isspace() for character in key):
            raise ValidationError(
                f"Argument key {key!r} for entrypoint {entrypoint!r} "
                "must be a flag name without '--'."
            )
        flag = f"--{key}"
        values = value if isinstance(value, list) else [value]
        for item in values:
            if item is None or item is False:
                continue
            if item is True:
                result.append(flag)
            elif isinstance(item, (str, int, float)):
                result.extend([flag, str(item)])
            else:
                raise ValidationError(
                    f"Unsupported value for argument {key!r} in entrypoint {entrypoint!r}."
                )
    return result


def _select_task_and_entrypoint(project: Project, value: str) -> tuple[str, str]:
    explicit_task, entrypoint = split_object(value)
    return resolve_task(project, explicit_task), entrypoint


def _select_experiment(
    project: Project, task_id: str, value: str | None
) -> tuple[Any | None, str | None]:
    branch_task, branch_experiment = branch_identity(project)
    if not value:
        if branch_task == task_id and branch_experiment:
            return (
                read_experiment(project, task_id, branch_experiment),
                branch_experiment,
            )
        return None, None
    explicit_task, experiment_id = split_object(value)
    if explicit_task and explicit_task != task_id:
        raise ValidationError(
            f"Conflicting task selectors: entrypoint uses {task_id!r}, "
            f"experiment uses {explicit_task!r}."
        )
    if branch_task == task_id and branch_experiment and branch_experiment != experiment_id:
        raise ValidationError(
            f"Experiment selector {experiment_id!r} conflicts with current experiment "
            f"branch for {branch_experiment!r}."
        )
    return read_experiment(project, task_id, experiment_id), experiment_id


def _match_invocation(record: Any, entrypoint: str, trailing: list[str]) -> None:
    requested = [entrypoint, *trailing]
    declared = [parse_invocation(value) for value in record.data["entrypoints"]]
    if requested not in declared:
        rendered = " ".join(requested)
        raise ValidationError(
            f"Launch invocation {rendered!r} is not declared by experiment "
            f"{record.task_id}:{record.id}."
        )


def launch(project: Project, args: argparse.Namespace) -> int:
    task_id, entrypoint_name = _select_task_and_entrypoint(project, args.entrypoint)
    read_task(project, task_id)
    experiment, experiment_id = _select_experiment(project, task_id, args.experiment)
    trailing = list(args.arguments)
    if experiment:
        _match_invocation(experiment, entrypoint_name, trailing)

    source_commit = ""
    if args.commit:
        source_commit = resolve_commit(project.root, args.commit)
        if experiment and not experiment.data["commit"]:
            raise ValidationError(
                f"Experiment {task_id}:{experiment_id} must be bound before a committed launch."
            )
    elif experiment and experiment.data["commit"]:
        source_commit = resolve_commit(project.root, experiment.data["commit"])
    if experiment and source_commit and experiment.data["commit"]:
        bound = resolve_commit(project.root, experiment.data["commit"])
        if source_commit != bound:
            raise ValidationError(
                f"Explicit commit {short_commit(source_commit)} does not match experiment "
                f"commit {short_commit(bound)}."
            )

    if source_commit:
        return _launch_committed(
            project, task_id, experiment_id or "", source_commit, entrypoint_name, trailing
        )
    return _execute(
        project,
        project.root,
        task_id,
        experiment_id or "",
        "",
        entrypoint_name,
        trailing,
    )


def _launch_committed(
    project: Project,
    task_id: str,
    experiment_id: str,
    source_commit: str,
    entrypoint: str,
    trailing: list[str],
) -> int:
    with tempfile.TemporaryDirectory(prefix="hiraku-worktree-") as temp:
        source_root = Path(temp) / "source"
        run_git(project.root, "worktree", "add", "--detach", str(source_root), source_commit)
        try:
            return _execute(
                project,
                source_root,
                task_id,
                experiment_id,
                source_commit,
                entrypoint,
                trailing,
            )
        finally:
            result = run_git(
                project.root, "worktree", "remove", "--force", str(source_root), check=False
            )
            if result.returncode:
                run_git(project.root, "worktree", "prune", check=False)


def _execute(
    project: Project,
    source_root: Path,
    task_id: str,
    experiment_id: str,
    source_commit: str,
    entrypoint_name: str,
    trailing: list[str],
) -> int:
    entrypoints = load_launch(source_root, project, task_id)
    if entrypoint_name not in entrypoints:
        raise HirakuError(f"Task {task_id!r} has no entrypoint {entrypoint_name!r}.")
    entrypoint = entrypoints[entrypoint_name]
    argv = [
        *entrypoint.command,
        *entrypoint.positionals,
        *render_named_args(entrypoint.args, entrypoint.name),
        *trailing,
    ]
    cwd = safe_relative(source_root, entrypoint.cwd, f"Entrypoint {entrypoint.name!r} cwd")
    if not cwd.is_dir():
        raise HirakuError(f"Entrypoint working directory does not exist: {cwd}")
    environment = os.environ.copy()
    environment.update(entrypoint.env)
    environment.update(
        {
            "HIRAKU_TASK_ID": task_id,
            "HIRAKU_EXPERIMENT_ID": experiment_id,
            "HIRAKU_SOURCE_COMMIT": source_commit,
            "HIRAKU_TRACKING_BACKEND": project.tracking_backend,
        }
    )
    if project.tracking_backend == "table":
        environment.pop("HIRAKU_MLFLOW_TRACKING_URI", None)
        environment.pop("HIRAKU_MLFLOW_EXPERIMENT", None)
        environment["HIRAKU_TABLE_PATH"] = str(project.runs_path(task_id))
    else:
        environment.pop("HIRAKU_TABLE_PATH", None)
        if not project.remote_mlflow:
            project.mlflow_tracking_db.parent.mkdir(parents=True, exist_ok=True)
        task = read_task(project, task_id)
        if not task.mlflow_experiment:
            raise ValidationError(
                f"Task {task_id!r} requires mlflow_experiment with tracking_backend: mlflow."
            )
        environment.update(
            {
                "HIRAKU_MLFLOW_TRACKING_URI": project.mlflow_tracking_uri,
                "HIRAKU_MLFLOW_EXPERIMENT": task.mlflow_experiment,
                "MLFLOW_TRACKING_URI": project.mlflow_tracking_uri,
                "MLFLOW_EXPERIMENT_NAME": task.mlflow_experiment,
            }
        )
    try:
        result = subprocess.run(argv, cwd=cwd, env=environment, check=False)
    except FileNotFoundError as exc:
        raise HirakuError(f"Entrypoint executable not found: {argv[0]}") from exc
    return result.returncode
