"""The production command-line interface for Hiraku."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from types import MappingProxyType
from typing import Any, TypeVar

import click
import yaml
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from .agent import (
    Action,
    Context,
    Program,
    list_actions,
    list_contexts,
    list_programs,
    load_action,
    load_context,
    load_program,
)
from .config import HirakuConfig
from .data import (
    Entrypoint,
    Experiment,
    Run,
    Task,
    list_experiments,
    list_tasks,
    load_experiment,
    load_task,
)
from .errors import (
    AmbiguousReferenceError,
    EntrypointNotFoundError,
    GitError,
    HirakuError,
    NoActiveExperimentError,
    NoActiveTaskError,
    RecordExistsError,
    ValidationError,
)
from .git import GitRepository
from .io import dump_yaml
from .launch import launch as launch_entrypoint
from .launch import render_arguments
from .records import Record
from .validation import validate_identifier

R = TypeVar("R", bound=Record)

try:
    _VERSION = package_version("hiraku")
except PackageNotFoundError:
    _VERSION = "0+unknown"


@dataclass(slots=True)
class CLIContext:
    """Per-invocation CLI state with lazy project discovery.

    Attributes:
        _config: Cached project configuration, populated on first access.
    """

    _config: HirakuConfig | None = None

    @property
    def config(self) -> HirakuConfig:
        """Return and cache the discovered project configuration."""
        if self._config is None:
            self._config = HirakuConfig.discover()
        return self._config


class HirakuGroup(click.Group):
    """Translate expected core failures at the outer process boundary."""

    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except PydanticValidationError as exc:
            raise click.ClickException(str(exc)) from exc
        except HirakuError as exc:
            raise click.ClickException(str(exc)) from exc


class OptionalBranchCommand(click.Command):
    """Normalize the documented optional-value experiment branch grammar."""

    _VALUE_OPTIONS = {
        "--task",
        "--summary",
        "--hypothesis",
        "--commit",
        "--from",
        "--markdown-file",
        "--set",
    }

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        normalized = list(args)
        for index, token in enumerate(normalized):
            if token.startswith("--branch="):
                value = token.partition("=")[2]
                if not value:
                    raise click.UsageError("--branch requires a non-empty value")
                normalized[index : index + 1] = ["--branch-name", value]
                break
            if token != "--branch":
                continue
            positionals = self._positionals(normalized)
            following = normalized[index + 1] if index + 1 < len(normalized) else None
            if len(positionals) > 1 and following and not following.startswith("-"):
                normalized[index : index + 2] = ["--branch-name", following]
            else:
                normalized[index] = "--branch-default"
            break
        return super().parse_args(ctx, normalized)

    @classmethod
    def _positionals(cls, args: Sequence[str]) -> list[str]:
        result: list[str] = []
        skip = False
        for token in args:
            if skip:
                skip = False
                continue
            if token in cls._VALUE_OPTIONS:
                skip = True
            elif token.startswith("--"):
                continue
            else:
                result.append(token)
        return result


def _json_value(value: Any) -> Any:
    if isinstance(value, Record):
        return {
            **{key: _json_value(item) for key, item in value.to_dict().items()},
            "markdown": value.markdown,
        }
    if isinstance(value, Run):
        return {
            "id": value.id,
            "metrics": _json_value(value.metrics),
            "params": _json_value(value.params),
            "task_id": value.task_id,
            "experiment_id": value.experiment_id,
            "commit": value.commit,
            "timestamp": _json_value(value.timestamp),
            "entrypoint": value.entrypoint,
            "url": value.url,
            "raw": _json_value(value.raw),
        }
    if isinstance(value, BaseModel):
        return _json_value(value.model_dump())
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, datetime):
        current = value if value.tzinfo else value.replace(tzinfo=UTC)
        return current.astimezone(UTC).isoformat()
    if isinstance(value, os.PathLike):
        return os.fspath(value)
    if isinstance(value, Mapping | MappingProxyType):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [_json_value(item) for item in value]
    return value


def _emit_json(value: Any) -> None:
    click.echo(json.dumps(_json_value(value), sort_keys=True, ensure_ascii=False))


def _plain(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return _json_value(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Mapping | list | tuple):
        return json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"))
    return str(value)


def _table(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    rendered = [[_plain(value) for value in row] for row in rows]
    widths = [len(column) for column in columns]
    for row in rendered:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))
    header = "  ".join(
        column.ljust(widths[index]) for index, column in enumerate(columns)
    )
    separator = "  ".join("-" * width for width in widths)
    lines = [header.rstrip(), separator.rstrip()]
    lines.extend(
        "  ".join(
            value.ljust(widths[index]) for index, value in enumerate(row)
        ).rstrip()
        for row in rendered
    )
    return "\n".join(lines)


def _show_record(record: Record, json_output: bool) -> None:
    if json_output:
        _emit_json(record)
    else:
        click.echo(
            f"---\n{dump_yaml(record.to_dict())}---\n{record.markdown}", nl=False
        )


def _parse_columns(value: str | None, defaults: Sequence[str]) -> list[str]:
    if value is None:
        return list(defaults)
    columns = [item.strip() for item in value.split(",")]
    if any(not item for item in columns):
        raise ValidationError("Column names must be non-empty")
    return columns


def _parse_names(value: str | None, label: str) -> list[str]:
    if value is None:
        return []
    names = [item.strip() for item in value.split(",")]
    if any(not item for item in names):
        raise ValidationError(f"{label} names must be non-empty")
    if len(set(names)) != len(names):
        raise ValidationError(f"Duplicate {label} name")
    return names


def _markdown(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValidationError(f"Cannot read Markdown file {path}: {exc}") from exc


def _field_changes(
    set_values: Sequence[str],
    unset_values: Sequence[str],
    dedicated: Mapping[str, Any],
    *,
    immutable: set[str],
) -> tuple[dict[str, Any], list[str]]:
    changes = {key: value for key, value in dedicated.items() if value is not None}
    seen: set[str] = set(changes)
    for expression in set_values:
        key, separator, raw = expression.partition("=")
        if not separator or not key:
            raise ValidationError("--set requires KEY=VALUE with a non-empty key")
        if key in seen:
            raise ValidationError(f"Field {key!r} was supplied more than once")
        if key in immutable:
            raise ValidationError(f"Field {key!r} is immutable")
        try:
            changes[key] = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise ValidationError(
                f"Invalid YAML value for field {key!r}: {exc}"
            ) from exc
        seen.add(key)
    removals: list[str] = []
    for key in unset_values:
        if not key:
            raise ValidationError("--unset requires a non-empty key")
        if key in seen:
            raise ValidationError(f"Field {key!r} cannot be set and unset together")
        if key in immutable:
            raise ValidationError(f"Field {key!r} is immutable")
        if key in removals:
            raise ValidationError(f"Field {key!r} was supplied more than once")
        removals.append(key)
    return changes, removals


def _reject_commit_set(set_values: Sequence[str]) -> None:
    for expression in set_values:
        key, separator, _ = expression.partition("=")
        if separator and key == "commit":
            raise ValidationError("Use --commit REF instead of --set commit=VALUE")


def _apply_update(
    record: R,
    *,
    set_values: Sequence[str],
    unset_values: Sequence[str],
    dedicated: Mapping[str, Any],
    markdown_file: Path | None,
    immutable: set[str],
) -> R:
    body = _markdown(markdown_file)
    changes, removals = _field_changes(
        set_values, unset_values, dedicated, immutable=immutable
    )
    for key, value in changes.items():
        setattr(record, key, value)
    for key in removals:
        field = type(record).model_fields.get(key)
        if field is not None:
            if field.is_required():
                raise ValidationError(f"Cannot unset required field: {key}")
            setattr(record, key, None)
        elif record.__pydantic_extra__ is not None:
            record.__pydantic_extra__.pop(key, None)
    if body is not None:
        record.markdown = body
    record.save()
    return record


def _edit_recoverable(path: Path, loader: Callable[[], R], label: str) -> R:
    """Open a canonical record path, even when its current contents are invalid."""
    try:
        loader()
    except (ValidationError, PydanticValidationError) as exc:
        if not path.is_file():
            raise
        click.echo(
            f"Warning: {label} is currently invalid: {exc}\n"
            f"Opening {path} for recovery.",
            err=True,
        )
    raw_editor = os.environ.get("EDITOR", "").strip() or "vim"
    try:
        editor = shlex.split(raw_editor)
    except ValueError as exc:
        raise ValidationError(f"Invalid EDITOR value: {exc}") from exc
    if not editor:
        raise ValidationError("EDITOR must contain a command")
    try:
        result = subprocess.run([*editor, str(path.absolute())], check=False)
    except OSError as exc:
        raise ValidationError(f"Could not run editor {editor[0]!r}: {exc}") from exc
    if result.returncode != 0:
        raise ValidationError(f"Editor exited with status {result.returncode}")
    return loader()


def _remove_empty_leaf(path: Path, expected: Path) -> None:
    if path == expected and path.is_dir():
        with suppress(OSError):
            path.rmdir()


def _active_task(config: HirakuConfig, supplied: str | None = None) -> Task:
    identifier, _ = _task_reference(config, supplied)
    return load_task(identifier, config=config)


def _task_reference(
    config: HirakuConfig, supplied: str | None = None
) -> tuple[str, Path]:
    identifier = supplied or config.active_task_id
    if identifier is None:
        raise NoActiveTaskError("No active task")
    validate_identifier(identifier, label="task ID")
    return identifier, config.tasks_dir / identifier / "task.md"


def _experiment_reference(
    config: HirakuConfig,
    reference: str | None,
    task_id: str | None = None,
) -> tuple[str, str, Path]:
    if reference is None:
        if config.active_experiment_id is None:
            raise NoActiveExperimentError("No active experiment")
        if task_id is not None and task_id != config.active_task_id:
            raise AmbiguousReferenceError(
                "Explicit task scope does not contain the active experiment"
            )
        reference = config.active_experiment_id
        task_id = task_id or config.active_task_id
    elif ":" in reference:
        qualified_task, reference = reference.split(":", 1)
        if task_id is not None and task_id != qualified_task:
            raise AmbiguousReferenceError(
                f"Conflicting task scopes for experiment {qualified_task}:{reference!s}"
            )
        task_id = qualified_task
    if task_id is None:
        task_id = config.active_task_id
    if task_id is None:
        raise NoActiveTaskError(f"No task scope for experiment {reference!r}")
    validate_identifier(task_id, label="task ID")
    validate_identifier(reference, label="experiment ID")
    path = config.tasks_dir / task_id / "experiments" / f"{reference}.md"
    return task_id, reference, path


def _experiment(
    config: HirakuConfig,
    reference: str | None,
    task_id: str | None = None,
) -> Experiment:
    if reference is None:
        if config.active_experiment_id is None:
            raise NoActiveExperimentError("No active experiment")
        if task_id is not None and task_id != config.active_task_id:
            raise AmbiguousReferenceError(
                "Explicit task scope does not contain the active experiment"
            )
        reference = config.active_experiment_id
        if task_id is None:
            task_id = config.active_task_id
    return load_experiment(reference, task=task_id, config=config)


def _action_reference(reference: str) -> tuple[str, str]:
    if reference.count("/") != 1:
        raise ValidationError("ACTION must have the form {entity}/{action_id}")
    entity, action_id = reference.split("/", 1)
    validate_identifier(entity, label="action entity")
    validate_identifier(action_id, label="action ID")
    return entity, action_id


def _record_rows(records: Sequence[Record], columns: Sequence[str]) -> list[list[Any]]:
    return [[record.to_dict().get(column) for column in columns] for record in records]


def _entrypoint_rows(entrypoints: Mapping[str, Entrypoint]) -> list[list[str]]:
    rows: list[list[str]] = []
    for name, entrypoint in entrypoints.items():
        command = [
            *entrypoint.command,
            *render_arguments(entrypoint.args),
        ]
        rows.append([name, shlex.join(command)])
    return rows


def _resolve_launch_entrypoint(
    task: Task,
    experiment: Experiment | None,
    name: str,
) -> Entrypoint:
    """Resolve the nearest entrypoint for a launch selector."""
    if experiment is not None and name in experiment.entrypoints:
        return experiment.entrypoints[name]
    try:
        return task.entrypoint(name)
    except EntrypointNotFoundError as exc:
        if experiment is None:
            raise
        raise EntrypointNotFoundError(
            f"Neither experiment {task.id}:{experiment.id} nor task "
            f"{task.id!r} has entrypoint {name!r}"
        ) from exc


@click.group(name="hiraku", cls=HirakuGroup)
@click.version_option(version=f"v{_VERSION}", message="%(version)s")
@click.pass_context
def main(ctx: click.Context) -> None:
    """Manage Hiraku projects, records, launches, and tracked runs."""
    ctx.ensure_object(CLIContext)


@main.command("init")
@click.option(
    "--tracking-backend",
    help="Set the default tracking backend.",
)
def init_command(tracking_backend: str | None) -> None:
    """Initialize a Hiraku project."""
    config = HirakuConfig.init(Path.cwd(), tracking_backend=tracking_backend)
    click.echo(f"Initialized Hiraku project at {config.path}")


@main.command("status")
@click.option("json_output", "--json", is_flag=True, help="Emit status as JSON.")
@click.pass_obj
def status_command(state: CLIContext, json_output: bool) -> None:
    """Show project and active-state information."""
    config = state.config
    try:
        branch = GitRepository(config).current_branch
    except GitError:
        branch = None
    result = {
        "project_root": config.root,
        "config_path": config.path,
        "active_task_id": config.active_task_id,
        "active_experiment_id": config.active_experiment_id,
        "default_program": config.default_program,
        "tracking_backend": config.tracking_backend,
        "git_branch": branch,
    }
    if json_output:
        _emit_json(result)
    else:
        rows = [
            (key, "unset" if value is None else value) for key, value in result.items()
        ]
        rows[-1] = ("git_branch", branch or "unavailable")
        click.echo(_table(("field", "value"), rows))


@main.group("task")
def task_group() -> None:
    """Manage task records and the active task."""


@task_group.command("create")
@click.argument("task_id")
@click.option("--goal")
@click.option("--question")
@click.option("--summary")
@click.option("markdown_file", "--markdown-file", type=click.Path(path_type=Path))
@click.option("set_values", "--set", multiple=True, metavar="KEY=VALUE")
@click.option("--branch")
@click.option("no_set", "--no-set", is_flag=True)
@click.pass_obj
def task_create(
    state: CLIContext,
    task_id: str,
    goal: str | None,
    question: str | None,
    summary: str | None,
    markdown_file: Path | None,
    set_values: tuple[str, ...],
    branch: str | None,
    no_set: bool,
) -> None:
    """Create and validate a task."""
    config = state.config
    validate_identifier(task_id, label="task ID")
    body = _markdown(markdown_file) or ""
    fields, _ = _field_changes(
        set_values,
        (),
        {
            "goal": goal,
            "question": question,
            "summary": summary,
            "branch": (
                branch
                if branch is not None
                else config.task_branch.format(task_id=task_id)
            ),
        },
        immutable={"id"},
    )
    repository = GitRepository(config)
    selected_branch = fields.pop("branch")
    repository.validate_branch(selected_branch)
    created = Task.create(
        task_id,
        markdown=body,
        activate=False,
        config=config,
        branch=selected_branch,
        **fields,
    )
    created_leaf = created.path.parent
    try:
        repository.create_task_branch(task_id, branch=selected_branch)
    except Exception:
        created.delete(config=config)
        _remove_empty_leaf(created_leaf, config.tasks_dir / task_id)
        raise
    if not no_set:
        config.set_active_task(created)
    click.echo(f"Created task {task_id} at {created.path}")


@task_group.command("list")
@click.option("--columns")
@click.option("json_output", "--json", is_flag=True)
@click.pass_obj
def task_list(state: CLIContext, columns: str | None, json_output: bool) -> None:
    """List tasks."""
    records = list_tasks(config=state.config)
    if json_output:
        _emit_json(records)
        return
    selected = _parse_columns(columns, ("id", "summary", "branch"))
    click.echo(_table(selected, _record_rows(records, selected)))


@task_group.command("show")
@click.argument("task_id", required=False)
@click.option("json_output", "--json", is_flag=True)
@click.pass_obj
def task_show(state: CLIContext, task_id: str | None, json_output: bool) -> None:
    """Show a task."""
    _show_record(_active_task(state.config, task_id), json_output)


@task_group.command("update")
@click.argument("task_id", required=False)
@click.option("--goal")
@click.option("--question")
@click.option("--summary")
@click.option("markdown_file", "--markdown-file", type=click.Path(path_type=Path))
@click.option("set_values", "--set", multiple=True, metavar="KEY=VALUE")
@click.option("unset_values", "--unset", multiple=True, metavar="KEY")
@click.pass_obj
def task_update(
    state: CLIContext,
    task_id: str | None,
    goal: str | None,
    question: str | None,
    summary: str | None,
    markdown_file: Path | None,
    set_values: tuple[str, ...],
    unset_values: tuple[str, ...],
) -> None:
    """Update and validate a task."""
    record = _active_task(state.config, task_id)
    _apply_update(
        record,
        set_values=set_values,
        unset_values=unset_values,
        dedicated={"goal": goal, "question": question, "summary": summary},
        markdown_file=markdown_file,
        immutable={"id"},
    )
    click.echo(f"Updated task {record.id}")


@task_group.command("edit")
@click.argument("task_id", required=False)
@click.pass_obj
def task_edit(state: CLIContext, task_id: str | None) -> None:
    """Edit a task in the default editor."""
    identifier, path = _task_reference(state.config, task_id)
    _edit_recoverable(
        path,
        lambda: load_task(identifier, config=state.config),
        f"Task {identifier!r}",
    )
    click.echo(f"Validated task {identifier}")


@task_group.command("delete")
@click.argument("task_id")
@click.pass_obj
def task_delete(state: CLIContext, task_id: str) -> None:
    """Delete a task that has no experiments."""
    config = state.config
    record = load_task(task_id, config=config)
    leaf = record.path.parent
    record.delete(config=config)
    if config.active_task_id == task_id:
        config.clear_active_task()
    _remove_empty_leaf(leaf, config.tasks_dir / task_id)
    click.echo(f"Deleted task {task_id}")


@task_group.command("set")
@click.argument("task_id")
@click.pass_obj
def task_set(state: CLIContext, task_id: str) -> None:
    """Set the active task."""
    state.config.set_active_task(task_id)
    click.echo(f"Active task: {task_id}")


@task_group.command("entrypoints")
@click.argument("task_id", required=False)
@click.option("json_output", "--json", is_flag=True)
@click.pass_obj
def task_entrypoints(state: CLIContext, task_id: str | None, json_output: bool) -> None:
    """List a task's entrypoint declarations."""
    values = _active_task(state.config, task_id).entrypoints
    if json_output:
        _emit_json(values)
    else:
        click.echo(_table(("name", "command"), _entrypoint_rows(values)))


@main.group("experiment")
def experiment_group() -> None:
    """Manage experiments and the active experiment."""


@experiment_group.command("create", cls=OptionalBranchCommand)
@click.argument("experiment_id")
@click.option("task_id", "--task")
@click.option("--summary")
@click.option("--hypothesis")
@click.option("--commit")
@click.option("branch_default", "--branch-default", is_flag=True, hidden=True)
@click.option("branch_name", "--branch-name", hidden=True)
@click.option("--branch", is_flag=True, help="Use the default experiment branch.")
@click.option("from_ref", "--from")
@click.option("markdown_file", "--markdown-file", type=click.Path(path_type=Path))
@click.option("set_values", "--set", multiple=True, metavar="KEY=VALUE")
@click.option("no_set", "--no-set", is_flag=True)
@click.pass_obj
def experiment_create(
    state: CLIContext,
    experiment_id: str,
    task_id: str | None,
    summary: str | None,
    hypothesis: str | None,
    commit: str | None,
    branch_default: bool,
    branch_name: str | None,
    branch: bool,
    from_ref: str | None,
    markdown_file: Path | None,
    set_values: tuple[str, ...],
    no_set: bool,
) -> None:
    """Create and validate an experiment."""
    config = state.config
    if ":" in experiment_id:
        qualified_task, unqualified = experiment_id.split(":", 1)
        if task_id is not None and task_id != qualified_task:
            raise AmbiguousReferenceError(
                f"Conflicting task scopes for experiment {experiment_id!r}"
            )
        task_id, experiment_id = qualified_task, unqualified
    task = _active_task(config, task_id)
    validate_identifier(experiment_id, label="experiment ID")
    body = _markdown(markdown_file) or ""
    _reject_commit_set(set_values)
    fields, _ = _field_changes(
        set_values,
        (),
        {"summary": summary, "hypothesis": hypothesis, "commit": commit},
        immutable={"id", "task_id", "branch"},
    )
    if "commit" in fields:
        commit = fields.pop("commit")
    wants_branch = branch_default or branch or branch_name is not None
    if from_ref is not None and not wants_branch:
        raise ValidationError("--from requires --branch")
    repository = GitRepository(config) if wants_branch or commit is not None else None
    selected_branch: str | None = None
    branch_exists = False
    if wants_branch:
        assert repository is not None
        selected_branch = branch_name or repository.experiment_branch_name(
            task.id, experiment_id
        )
        repository.validate_branch(selected_branch)
        branch_exists = repository.branch_exists(selected_branch)
        if branch_exists and from_ref is not None:
            raise ValidationError("--from cannot be used with an existing branch")
    resolved_commit = (
        repository.resolve(commit)
        if repository is not None and commit is not None
        else None
    )
    created = Experiment.create(
        experiment_id,
        task=task,
        commit=resolved_commit,
        markdown=body,
        activate=False,
        config=config,
        branch=selected_branch,
        **fields,
    )
    try:
        if wants_branch and not branch_exists:
            assert repository is not None and selected_branch is not None
            source = from_ref or task.branch or repository.task_branch_name(task.id)
            repository.create_experiment_branch(
                task.id,
                experiment_id,
                branch=selected_branch,
                start_ref=source,
            )
    except Exception:
        created.delete()
        raise
    if not no_set:
        config.set_active_experiment(created)
    click.echo(f"Created experiment {task.id}:{experiment_id} at {created.path}")


@experiment_group.command("list")
@click.option("task_id", "--task")
@click.option("--columns")
@click.option("json_output", "--json", is_flag=True)
@click.pass_obj
def experiment_list(
    state: CLIContext, task_id: str | None, columns: str | None, json_output: bool
) -> None:
    """List experiments for a task."""
    records = list_experiments(
        task=_active_task(state.config, task_id), config=state.config
    )
    if json_output:
        _emit_json(records)
        return
    selected = _parse_columns(columns, ("id", "summary", "commit", "branch"))
    rows = _record_rows(records, selected)
    commit_columns = [
        index for index, column in enumerate(selected) if column == "commit"
    ]
    if commit_columns:
        abbreviations = _abbreviate_values(
            [record.commit for record in records if record.commit is not None]
        )
        for record, row in zip(records, rows, strict=True):
            for index in commit_columns:
                row[index] = abbreviations.get(record.commit, "")
    click.echo(_table(selected, rows))


@experiment_group.command("show")
@click.argument("experiment_id", required=False)
@click.option("task_id", "--task")
@click.option("json_output", "--json", is_flag=True)
@click.pass_obj
def experiment_show(
    state: CLIContext,
    experiment_id: str | None,
    task_id: str | None,
    json_output: bool,
) -> None:
    """Show an experiment."""
    _show_record(_experiment(state.config, experiment_id, task_id), json_output)


@experiment_group.command("update")
@click.argument("experiment_id", required=False)
@click.option("task_id", "--task")
@click.option("--summary")
@click.option("--hypothesis")
@click.option("--commit")
@click.option("markdown_file", "--markdown-file", type=click.Path(path_type=Path))
@click.option("set_values", "--set", multiple=True, metavar="KEY=VALUE")
@click.option("unset_values", "--unset", multiple=True, metavar="KEY")
@click.pass_obj
def experiment_update(
    state: CLIContext,
    experiment_id: str | None,
    task_id: str | None,
    summary: str | None,
    hypothesis: str | None,
    commit: str | None,
    markdown_file: Path | None,
    set_values: tuple[str, ...],
    unset_values: tuple[str, ...],
) -> None:
    """Update and validate an experiment."""
    config = state.config
    record = _experiment(config, experiment_id, task_id)
    _reject_commit_set(set_values)
    resolved_commit = (
        GitRepository(config).resolve(commit) if commit is not None else None
    )
    _apply_update(
        record,
        set_values=set_values,
        unset_values=unset_values,
        dedicated={
            "summary": summary,
            "hypothesis": hypothesis,
            "commit": resolved_commit,
        },
        markdown_file=markdown_file,
        immutable={"id", "task_id"},
    )
    click.echo(f"Updated experiment {record.task_id}:{record.id}")


@experiment_group.command("edit")
@click.argument("experiment_id", required=False)
@click.option("task_id", "--task")
@click.pass_obj
def experiment_edit(
    state: CLIContext, experiment_id: str | None, task_id: str | None
) -> None:
    """Edit an experiment in the default editor."""
    selected_task, identifier, path = _experiment_reference(
        state.config, experiment_id, task_id
    )
    _edit_recoverable(
        path,
        lambda: load_experiment(identifier, task=selected_task, config=state.config),
        f"Experiment {selected_task}:{identifier}",
    )
    click.echo(f"Validated experiment {selected_task}:{identifier}")


@experiment_group.command("delete")
@click.argument("experiment_id")
@click.option("task_id", "--task")
@click.pass_obj
def experiment_delete(
    state: CLIContext, experiment_id: str, task_id: str | None
) -> None:
    """Delete an experiment record."""
    config = state.config
    record = _experiment(config, experiment_id, task_id)
    record.delete()
    if (
        config.active_task_id == record.task_id
        and config.active_experiment_id == record.id
    ):
        config.clear_active_experiment()
    click.echo(f"Deleted experiment {record.task_id}:{record.id}")


@experiment_group.command("set")
@click.argument("experiment_id")
@click.option("task_id", "--task")
@click.pass_obj
def experiment_set(state: CLIContext, experiment_id: str, task_id: str | None) -> None:
    """Activate an experiment and its task."""
    record = _experiment(state.config, experiment_id, task_id)
    state.config.set_active_experiment(record)
    click.echo(f"Active experiment: {record.task_id}:{record.id}")


@experiment_group.command("clear")
@click.pass_obj
def experiment_clear(state: CLIContext) -> None:
    """Clear the active experiment."""
    state.config.clear_active_experiment()
    click.echo("Active experiment cleared")


@experiment_group.command("entrypoints")
@click.argument("experiment_id", required=False)
@click.option("task_id", "--task")
@click.option("json_output", "--json", is_flag=True)
@click.pass_obj
def experiment_entrypoints(
    state: CLIContext,
    experiment_id: str | None,
    task_id: str | None,
    json_output: bool,
) -> None:
    """List an experiment's effective inherited entrypoints."""
    experiment = _experiment(state.config, experiment_id, task_id)
    task = load_task(experiment.task_id, config=state.config)
    values = {**task.entrypoints, **experiment.entrypoints}
    sources = {
        name: "experiment" if name in experiment.entrypoints else "task"
        for name in values
    }
    if json_output:
        _emit_json(
            {
                name: {**entrypoint.model_dump(mode="json"), "source": sources[name]}
                for name, entrypoint in values.items()
            }
        )
    else:
        rows = [[*row, sources[row[0]]] for row in _entrypoint_rows(values)]
        click.echo(_table(("name", "command", "source"), rows))


main.add_command(experiment_group, "exp")


@main.group("proposal")
def proposal_group() -> None:
    """Inspect proposed experiments."""


@proposal_group.command("list")
@click.option("task_id", "--task")
@click.option("json_output", "--json", is_flag=True)
@click.pass_obj
def proposal_list(state: CLIContext, task_id: str | None, json_output: bool) -> None:
    """List experiments without a commit."""
    task = _active_task(state.config, task_id)
    records = [
        record
        for record in list_experiments(task=task, config=state.config)
        if record.commit is None
    ]
    if json_output:
        _emit_json(records)
    else:
        columns = ("id", "summary", "branch")
        click.echo(_table(columns, _record_rows(records, columns)))


def _install_record_group(
    name: str,
    noun: str,
    record_type: type[R],
    loader: Any,
    lister: Any,
    directory: Any,
    filename: str,
) -> click.Group:
    @main.group(name=name, help=f"Manage {noun} records.")
    def group() -> None:
        pass

    @group.command("create", help=f"Create and validate a {noun}.")
    @click.argument(f"{noun}_id")
    @click.option("markdown_file", "--markdown-file", type=click.Path(path_type=Path))
    @click.option("set_values", "--set", multiple=True, metavar="KEY=VALUE")
    @click.pass_obj
    def create_record(
        state: CLIContext,
        markdown_file: Path | None,
        set_values: tuple[str, ...],
        **arguments: str,
    ) -> None:
        identifier = arguments[f"{noun}_id"]
        validate_identifier(identifier, label=f"{noun} ID")
        body = _markdown(markdown_file) or ""
        fields, _ = _field_changes(set_values, (), {}, immutable={"id"})
        record = record_type(id=identifier, markdown=body, **fields)
        path = directory(state.config) / identifier / filename
        if path.exists():
            raise RecordExistsError(
                f"{record_type.__name__} record already exists: {path}"
            )
        record.save(path)
        click.echo(f"Created {noun} {identifier} at {path}")

    @group.command("list", help=f"List {noun}s.")
    @click.option("json_output", "--json", is_flag=True)
    @click.pass_obj
    def list_records(state: CLIContext, json_output: bool) -> None:
        records = lister(config=state.config)
        if json_output:
            _emit_json(records)
        else:
            click.echo(_table(("id",), [[record.id] for record in records]))

    @group.command("show", help=f"Show a {noun}.")
    @click.argument(f"{noun}_id")
    @click.option("json_output", "--json", is_flag=True)
    @click.pass_obj
    def show_record(state: CLIContext, json_output: bool, **arguments: str) -> None:
        record = loader(arguments[f"{noun}_id"], config=state.config)
        _show_record(record, json_output)

    @group.command("update", help=f"Update and validate a {noun}.")
    @click.argument(f"{noun}_id")
    @click.option("markdown_file", "--markdown-file", type=click.Path(path_type=Path))
    @click.option("set_values", "--set", multiple=True, metavar="KEY=VALUE")
    @click.option("unset_values", "--unset", multiple=True, metavar="KEY")
    @click.pass_obj
    def update_record(
        state: CLIContext,
        markdown_file: Path | None,
        set_values: tuple[str, ...],
        unset_values: tuple[str, ...],
        **arguments: str,
    ) -> None:
        record = loader(arguments[f"{noun}_id"], config=state.config)
        _apply_update(
            record,
            set_values=set_values,
            unset_values=unset_values,
            dedicated={},
            markdown_file=markdown_file,
            immutable={"id"},
        )
        click.echo(f"Updated {noun} {record.id}")

    @group.command("edit", help=f"Edit a {noun} in the default editor.")
    @click.argument(f"{noun}_id")
    @click.pass_obj
    def edit_record(state: CLIContext, **arguments: str) -> None:
        identifier = arguments[f"{noun}_id"]
        validate_identifier(identifier, label=f"{noun} ID")
        path = directory(state.config) / identifier / filename
        _edit_recoverable(
            path,
            lambda: loader(identifier, config=state.config),
            f"{record_type.__name__} {identifier!r}",
        )
        click.echo(f"Validated {noun} {identifier}")

    @group.command("delete", help=f"Delete a {noun} record.")
    @click.argument(f"{noun}_id")
    @click.pass_obj
    def delete_record(state: CLIContext, **arguments: str) -> None:
        identifier = arguments[f"{noun}_id"]
        record = loader(identifier, config=state.config)
        leaf = record.path.parent
        record.delete()
        _remove_empty_leaf(leaf, directory(state.config) / identifier)
        click.echo(f"Deleted {noun} {identifier}")

    return group


_install_record_group(
    "program",
    "program",
    Program,
    load_program,
    list_programs,
    lambda config: config.programs_dir,
    "program.md",
)
_install_record_group(
    "context",
    "context",
    Context,
    load_context,
    list_contexts,
    lambda config: config.contexts_dir,
    "context.md",
)


@main.group("action")
def action_group() -> None:
    """Manage action records."""


@action_group.command("create", help="Create and validate an action.")
@click.argument("action")
@click.option("markdown_file", "--markdown-file", type=click.Path(path_type=Path))
@click.option("set_values", "--set", multiple=True, metavar="KEY=VALUE")
@click.pass_obj
def action_create(
    state: CLIContext,
    action: str,
    markdown_file: Path | None,
    set_values: tuple[str, ...],
) -> None:
    entity, identifier = _action_reference(action)
    body = _markdown(markdown_file) or ""
    fields, _ = _field_changes(set_values, (), {}, immutable={"id", "entity"})
    record = Action(id=identifier, entity=entity, markdown=body, **fields)
    path = state.config.actions_dir / entity / identifier / "action.md"
    if path.exists():
        raise RecordExistsError(f"Action record already exists: {path}")
    record.save(path)
    click.echo(f"Created action {action} at {path}")


@action_group.command("list", help="List actions.")
@click.option("--entity")
@click.option("json_output", "--json", is_flag=True)
@click.pass_obj
def action_list(state: CLIContext, entity: str | None, json_output: bool) -> None:
    records = list_actions(entity=entity, config=state.config)
    if json_output:
        _emit_json(records)
    else:
        click.echo(_table(("entity", "id"), [[r.entity, r.id] for r in records]))


def _load_action_reference(reference: str, config: HirakuConfig) -> Action:
    entity, identifier = _action_reference(reference)
    return load_action(identifier, entity=entity, config=config)


@action_group.command("show", help="Show an action.")
@click.argument("action")
@click.option("json_output", "--json", is_flag=True)
@click.pass_obj
def action_show(state: CLIContext, action: str, json_output: bool) -> None:
    _show_record(_load_action_reference(action, state.config), json_output)


@action_group.command("update", help="Update and validate an action.")
@click.argument("action")
@click.option("markdown_file", "--markdown-file", type=click.Path(path_type=Path))
@click.option("set_values", "--set", multiple=True, metavar="KEY=VALUE")
@click.option("unset_values", "--unset", multiple=True, metavar="KEY")
@click.pass_obj
def action_update(
    state: CLIContext,
    action: str,
    markdown_file: Path | None,
    set_values: tuple[str, ...],
    unset_values: tuple[str, ...],
) -> None:
    record = _load_action_reference(action, state.config)
    _apply_update(
        record,
        set_values=set_values,
        unset_values=unset_values,
        dedicated={},
        markdown_file=markdown_file,
        immutable={"id", "entity"},
    )
    click.echo(f"Updated action {action}")


@action_group.command("edit", help="Edit an action in the default editor.")
@click.argument("action")
@click.pass_obj
def action_edit(state: CLIContext, action: str) -> None:
    entity, identifier = _action_reference(action)
    path = state.config.actions_dir / entity / identifier / "action.md"
    _edit_recoverable(
        path,
        lambda: _load_action_reference(action, state.config),
        f"Action {action!r}",
    )
    click.echo(f"Validated action {action}")


@action_group.command("delete", help="Delete an action record.")
@click.argument("action")
@click.pass_obj
def action_delete(state: CLIContext, action: str) -> None:
    entity, identifier = _action_reference(action)
    record = load_action(identifier, entity=entity, config=state.config)
    leaf = record.path.parent
    record.delete()
    _remove_empty_leaf(leaf, state.config.actions_dir / entity / identifier)
    click.echo(f"Deleted action {action}")


def _parse_overrides(tokens: Sequence[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--" or not token.startswith("--") or token == "--":
            raise ValidationError(f"Invalid entrypoint override token: {token!r}")
        raw = token[2:]
        if not raw:
            raise ValidationError("Entrypoint override key must be non-empty")
        if "=" in raw:
            key, value = raw.split("=", 1)
        else:
            key = raw
            if index + 1 < len(tokens) and not tokens[index + 1].startswith("--"):
                index += 1
                value = tokens[index]
            else:
                value = True
        key = key.replace("-", "_")
        if not key:
            raise ValidationError("Entrypoint override key must be non-empty")
        if key in result:
            previous = result[key]
            result[key] = (
                [*previous, value] if isinstance(previous, list) else [previous, value]
            )
        else:
            result[key] = value
        index += 1
    return result


@main.command("launch")
@click.argument("target")
@click.argument("entrypoint_overrides", nargs=-1, type=click.UNPROCESSED)
@click.option("task_id", "--task")
@click.option("--commit")
@click.option("--dispatch")
@click.pass_obj
def launch_command(
    state: CLIContext,
    target: str,
    entrypoint_overrides: tuple[str, ...],
    task_id: str | None,
    commit: str | None,
    dispatch: str | None,
) -> None:
    """Resolve and launch an entrypoint."""
    config = state.config
    task = _active_task(config, task_id)
    attributed: Experiment | None = None
    if ":" in target:
        declared_id, entrypoint_name = target.split(":", 1)
        attributed = load_experiment(declared_id, task=task, config=config)
        entrypoint = _resolve_launch_entrypoint(task, attributed, entrypoint_name)
    else:
        entrypoint_name = target
        entrypoint = task.entrypoint(entrypoint_name)
    parsed_overrides = _parse_overrides(entrypoint_overrides)
    overrides: dict[str, Any] = {}
    for key, value in parsed_overrides.items():
        matches = [
            declared
            for declared in entrypoint.args
            if declared.replace("-", "_") == key
        ]
        if len(matches) > 1:
            raise ValidationError(
                f"Entrypoint {target} declares ambiguous argument names for "
                f"--{key.replace('_', '-')}"
            )
        resolved_key = matches[0] if matches else key
        overrides[resolved_key] = value
        if not matches:
            click.echo(
                f"Warning: argument --{key.replace('_', '-')} is not declared by "
                f"entrypoint {target}",
                err=True,
            )
    result = launch_entrypoint(
        entrypoint,
        name=entrypoint_name,
        args=overrides,
        task_id=task.id,
        experiment_id=None if attributed is None else attributed.id,
        commit=commit,
        dispatch=dispatch,
        check=False,
        config=config,
    )
    if result.returncode:
        raise click.exceptions.Exit(result.returncode)


@main.group("run")
def run_group() -> None:
    """Query tracked runs."""


def _abbreviate_values(values: Sequence[str]) -> dict[str, str]:
    distinct = sorted(set(values))
    result: dict[str, str] = {}
    for value in distinct:
        length = min(8, len(value))
        while (
            length < len(value)
            and sum(other.startswith(value[:length]) for other in distinct) > 1
        ):
            length += 1
        result[value] = value[:length]
    return result


def _human_timestamp(value: datetime | None) -> str:
    if value is None:
        return ""
    current = value if value.tzinfo else value.replace(tzinfo=UTC)
    current = current.astimezone(UTC)
    return current.isoformat(sep=" ", timespec="seconds").replace("+00:00", "Z")


@run_group.command("list")
@click.option("task_id", "--task")
@click.option("experiment_id", "--experiment")
@click.option("--metrics")
@click.option("--params")
@click.option("sort_name", "--sort")
@click.option("--ascending", is_flag=True)
@click.option("--descending", is_flag=True)
@click.option("--limit", type=click.IntRange(min=0))
@click.option("json_output", "--json", is_flag=True)
@click.pass_obj
def run_list(
    state: CLIContext,
    task_id: str | None,
    experiment_id: str | None,
    metrics: str | None,
    params: str | None,
    sort_name: str | None,
    ascending: bool,
    descending: bool,
    limit: int | None,
    json_output: bool,
) -> None:
    """List normalized runs for a task."""
    if ascending and descending:
        raise ValidationError("--ascending and --descending are mutually exclusive")
    if (ascending or descending) and sort_name is None:
        raise ValidationError("A sort direction requires --sort")
    task = _active_task(state.config, task_id)
    selected_experiment = (
        load_experiment(experiment_id, task=task, config=state.config)
        if experiment_id is not None
        else None
    )
    metric_names = _parse_names(metrics, "metric")
    param_names = _parse_names(params, "parameter")
    declarations = selected_experiment.metrics if selected_experiment else task.metrics
    if metrics is None:
        metric_names = [item.name for item in declarations]
    query: dict[str, Any] = {}
    if sort_name is not None:
        query["sort"] = sort_name
        if ascending or descending:
            query["ascending"] = ascending
        else:
            directions = {item.name: item.direction for item in declarations}
            query["ascending"] = directions.get(sort_name) != "maximize"
    if limit is not None:
        query["limit"] = limit
    runs = (
        selected_experiment.runs(config=state.config, **query)
        if selected_experiment
        else task.runs(config=state.config, **query)
    )
    if json_output:
        _emit_json(runs)
        return
    abbreviations = _abbreviate_values([run.id for run in runs])
    commit_abbreviations = _abbreviate_values(
        [run.commit for run in runs if run.commit is not None]
    )
    include_experiment = any(run.experiment_id is not None for run in runs)
    columns = ["id"]
    if include_experiment:
        columns.append("experiment")
    columns.extend(("timestamp", "commit"))
    columns.extend(f"param:{name}" for name in param_names)
    columns.extend(f"metric:{name}" for name in metric_names)
    rows: list[list[Any]] = []
    for run in runs:
        row: list[Any] = [abbreviations[run.id]]
        if include_experiment:
            row.append(run.experiment_id)
        row.extend(
            (
                _human_timestamp(run.timestamp),
                commit_abbreviations.get(run.commit, ""),
            )
        )
        row.extend(run.params.get(name) for name in param_names)
        row.extend(run.metrics.get(name) for name in metric_names)
        rows.append(row)
    click.echo(_table(columns, rows))


if __name__ == "__main__":
    main()
