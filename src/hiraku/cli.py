"""Command-line interface for Hiraku projects."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
from .data import Run
from .errors import GitError, HirakuError, NoActiveRecordError, ValidationError
from .git import GitRepository
from .io import dump_yaml
from .launch import _dispatcher, launch, render_arguments
from .records import BaseRecord, Record, list_records, load_record
from .validation import validate_identifier


class CLIContext:
    def __init__(self) -> None:
        self._config: HirakuConfig | None = None

    @property
    def config(self) -> HirakuConfig:
        if self._config is None:
            self._config = HirakuConfig.discover()
        return self._config


class HirakuGroup(click.Group):
    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except (HirakuError, PydanticValidationError) as exc:
            raise click.ClickException(str(exc)) from exc


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseRecord):
        return {**_json_value(value.to_dict()), "markdown": value.markdown}
    if isinstance(value, Run):
        return {
            "id": value.id,
            "metrics": _json_value(value.metrics),
            "params": _json_value(value.params),
            "record_id": value.record_id,
            "commit": value.commit,
            "timestamp": _json_value(value.timestamp),
            "entrypoint": value.entrypoint,
            "url": value.url,
            "raw": _json_value(value.raw),
        }
    if isinstance(value, BaseModel):
        return _json_value(value.model_dump())
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [_json_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _emit_json(value: Any) -> None:
    click.echo(json.dumps(_json_value(value), sort_keys=True, ensure_ascii=False))


def _table(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    rendered = [["" if item is None else str(item) for item in row] for row in rows]
    widths = [
        max(len(name), *(len(row[index]) for row in rendered))
        for index, name in enumerate(columns)
    ]
    lines = ["  ".join(name.ljust(widths[i]) for i, name in enumerate(columns))]
    lines.append("  ".join("-" * width for width in widths))
    lines.extend(
        "  ".join(item.ljust(widths[i]) for i, item in enumerate(row)).rstrip()
        for row in rendered
    )
    return "\n".join(lines)


def _fields(values: Sequence[str]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for expression in values:
        key, separator, raw = expression.partition("=")
        if not separator or not key:
            raise ValidationError("--set requires KEY=VALUE")
        if key in fields:
            raise ValidationError(f"Field {key!r} was supplied more than once")
        try:
            fields[key] = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise ValidationError(f"Invalid YAML for {key!r}: {exc}") from exc
    return fields


def _names(raw: str | None, label: str) -> list[str]:
    if raw is None:
        return []
    names = [item.strip() for item in raw.split(",")]
    if any(not name for name in names) or len(names) != len(set(names)):
        raise ValidationError(f"Invalid {label} names")
    return names


def _markdown(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError(f"Cannot read {path}: {exc}") from exc


def _active(config: HirakuConfig, record_id: str | None = None) -> Record:
    identifier = record_id or config.active_record_id
    if identifier is None:
        raise NoActiveRecordError("No active record; supply --record or set one")
    return load_record(identifier, config=config)


def _edit(path: Path) -> None:
    editor = shlex.split(os.environ.get("EDITOR", "vim"))
    if not editor:
        raise ValidationError("EDITOR must contain a command")
    result = subprocess.run([*editor, str(path)], check=False)
    if result.returncode:
        raise ValidationError(f"Editor exited with status {result.returncode}")


def _edit_recoverable(
    path: Path, loader: Callable[[], BaseRecord], label: str
) -> BaseRecord:
    try:
        loader()
    except (ValidationError, PydanticValidationError) as exc:
        if not path.is_file():
            raise
        click.echo(
            f"Warning: {label} is invalid: {exc}\nOpening {path} for recovery.",
            err=True,
        )
    _edit(path)
    return loader()


def _overrides(tokens: Sequence[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not token.startswith("--") or token == "--":
            raise ValidationError(f"Invalid entrypoint argument: {token!r}")
        key, separator, raw = token[2:].partition("=")
        if not key:
            raise ValidationError("Entrypoint argument name must be non-empty")
        if not separator:
            if index + 1 < len(tokens) and not tokens[index + 1].startswith("--"):
                index += 1
                raw = tokens[index]
            else:
                raw = True
        key = key.replace("-", "_")
        if key in result:
            previous = result[key]
            result[key] = (
                [*previous, raw] if isinstance(previous, list) else [previous, raw]
            )
        else:
            result[key] = raw
        index += 1
    return result


def _update_fields(
    record: BaseRecord,
    set_values: Sequence[str],
    unset_values: Sequence[str],
    markdown_file: Path | None,
    *,
    immutable: set[str] | None = None,
) -> None:
    changes = _fields(set_values)
    protected = immutable or {"id"}
    if set(changes).intersection(protected) or set(unset_values).intersection(
        protected
    ):
        raise ValidationError(f"Cannot change {sorted(protected)}")
    if set(changes).intersection(unset_values):
        raise ValidationError("A field cannot be set and unset together")
    for key, value in changes.items():
        setattr(record, key, value)
    for key in unset_values:
        if key in type(record).model_fields:
            record.__dict__.pop(key, None)
            record.__pydantic_fields_set__.discard(key)
        elif record.__pydantic_extra__ is not None:
            record.__pydantic_extra__.pop(key, None)
    if markdown_file is not None:
        record.markdown = _markdown(markdown_file)
    record.save()


@click.group(name="hiraku", cls=HirakuGroup)
@click.pass_context
def main(ctx: click.Context) -> None:
    """Manage research records, launches, and tracked runs."""
    ctx.ensure_object(CLIContext)


@main.command("init")
@click.argument("path", required=False, type=click.Path(path_type=Path))
@click.option("--tracking-backend")
@click.option("--template", type=click.Path(path_type=Path))
@click.option("--example")
@click.option("--ignore-git", is_flag=True)
def init_command(
    path: Path | None,
    tracking_backend: str | None,
    template: Path | None,
    example: str | None,
    ignore_git: bool,
) -> None:
    """Initialize a project."""
    if template is not None and example is not None:
        raise click.UsageError("--template and --example are mutually exclusive")
    config = HirakuConfig.init(
        path or Path.cwd(),
        tracking_backend=tracking_backend,
        template=template,
        example=example,
        ignore_git=ignore_git,
    )
    click.echo(f"Initialized Hiraku project at {config.path}")


@main.command("status")
@click.option("--json", "json_output", is_flag=True)
@click.pass_obj
def status_command(state: CLIContext, json_output: bool) -> None:
    """Show project and active record."""
    config = state.config
    try:
        branch = GitRepository(config).current_branch
    except GitError:
        branch = None
    result = {
        "project_root": config.root,
        "active_record_id": config.active_record_id,
        "tracking_backend": config.tracking_backend,
        "git_branch": branch,
    }
    if json_output:
        _emit_json(result)
    else:
        click.echo(_table(("field", "value"), list(result.items())))


@main.group("record")
def record_group() -> None:
    """Manage research records."""


@record_group.command("create")
@click.argument("record_id")
@click.option("--parent", "parent_id")
@click.option("--related-to", multiple=True)
@click.option("--type")
@click.option("--commit")
@click.option("--markdown-file", type=click.Path(path_type=Path))
@click.option("--set", "set_values", multiple=True, metavar="KEY=VALUE")
@click.option("--no-activate", is_flag=True)
@click.pass_obj
def record_create(
    state: CLIContext,
    record_id: str,
    parent_id: str | None,
    related_to: tuple[str, ...],
    type: str | None,
    commit: str | None,
    markdown_file: Path | None,
    set_values: tuple[str, ...],
    no_activate: bool,
) -> None:
    """Create a root or child record."""
    fields = _fields(set_values)
    if any(key in fields for key in ("id", "parent_id", "related_to")):
        raise ValidationError("Use dedicated options for id, parent_id and related_to")
    if type is not None:
        fields["type"] = type
    if commit is not None:
        fields["commit"] = GitRepository(state.config).resolve(commit)
    record = Record.create(
        record_id,
        parent_id=parent_id,
        related_to=list(related_to),
        markdown=_markdown(markdown_file),
        activate=not no_activate,
        config=state.config,
        **fields,
    )
    click.echo(f"Created record {record.id} at {record.path}")


@record_group.command("list")
@click.option("--json", "json_output", is_flag=True)
@click.pass_obj
def record_list(state: CLIContext, json_output: bool) -> None:
    """List all research records."""
    records = list_records(config=state.config)
    if json_output:
        _emit_json(records)
    else:
        click.echo(
            _table(
                ("id", "parent", "type"), [(r.id, r.parent_id, r.type) for r in records]
            )
        )


@record_group.command("show")
@click.argument("record_id", required=False)
@click.option("--raw", is_flag=True)
@click.option("--json", "json_output", is_flag=True)
@click.pass_obj
def record_show(
    state: CLIContext, record_id: str | None, raw: bool, json_output: bool
) -> None:
    """Show stored or effective fields with their sources."""
    record = _active(state.config, record_id)
    if raw:
        values = record.to_dict()
        sources = {}
    else:
        resolved = record.resolve(config=state.config)
        values, sources = resolved.values, resolved.sources
    if json_output:
        _emit_json({"values": values, "sources": sources, "markdown": record.markdown})
    else:
        click.echo(
            f"---\n{dump_yaml(_json_value(values))}---\n{record.markdown}", nl=False
        )
        if sources:
            click.echo("\nSources:\n" + dump_yaml(sources), nl=False)


@record_group.command("update")
@click.argument("record_id")
@click.option("--parent", "parent_id")
@click.option("--root", "make_root", is_flag=True)
@click.option("--related-to", multiple=True)
@click.option("--commit")
@click.option("--set", "set_values", multiple=True, metavar="KEY=VALUE")
@click.option("--unset", "unset_values", multiple=True)
@click.option("--markdown-file", type=click.Path(path_type=Path))
@click.pass_obj
def record_update(
    state: CLIContext,
    record_id: str,
    parent_id: str | None,
    make_root: bool,
    related_to: tuple[str, ...],
    commit: str | None,
    set_values: tuple[str, ...],
    unset_values: tuple[str, ...],
    markdown_file: Path | None,
) -> None:
    """Update stored fields; --unset removes an override."""
    record = load_record(record_id, config=state.config)
    if parent_id is not None and make_root:
        raise ValidationError("--parent and --root are mutually exclusive")
    changes = _fields(set_values)
    if any(key in changes for key in ("id", "parent_id")):
        raise ValidationError("id and parent_id require dedicated operations")
    if related_to:
        changes["related_to"] = list(related_to)
    if commit is not None:
        changes["commit"] = GitRepository(state.config).resolve(commit)
    fields = record.to_dict()
    fields.update(changes)
    for key in unset_values:
        if key in {"id", "parent_id"}:
            raise ValidationError(f"Cannot unset {key}")
        if key in changes:
            raise ValidationError(f"Cannot set and unset {key!r} together")
        fields.pop(key, None)
    new_parent = None if make_root else parent_id
    if make_root or parent_id is not None:
        fields["parent_id"] = new_parent
    body = _markdown(markdown_file) if markdown_file is not None else record.markdown
    candidate = Record.model_validate({**fields, "markdown": body})
    candidate.path = record.path
    candidate._persisted_id = record.id
    if make_root or parent_id is not None:
        candidate.reparent(new_parent, config=state.config)
    else:
        candidate.save()
    click.echo(f"Updated record {record_id}")


@record_group.command("edit")
@click.argument("record_id")
@click.pass_obj
def record_edit(state: CLIContext, record_id: str) -> None:
    """Edit a record's stored Markdown file."""
    validate_identifier(record_id)
    paths = list(state.config.records_dir.glob(f"*/{record_id}.md"))
    if len(paths) != 1:
        raise ValidationError(f"Expected one record path for {record_id!r}")
    _edit_recoverable(
        paths[0],
        lambda: load_record(record_id, config=state.config),
        f"Record {record_id!r}",
    )
    click.echo(f"Validated record {record_id}")


@record_group.command("delete")
@click.argument("record_id")
@click.pass_obj
def record_delete(state: CLIContext, record_id: str) -> None:
    """Delete a leaf record."""
    record = load_record(record_id, config=state.config)
    parent = record.path.parent
    record.delete(config=state.config)
    if state.config.active_record_id == record_id:
        state.config.clear_active_record()
    with suppress(OSError):
        parent.rmdir()
    click.echo(f"Deleted record {record_id}")


@record_group.command("set")
@click.argument("record_id")
@click.pass_obj
def record_set(state: CLIContext, record_id: str) -> None:
    """Set the active record."""
    state.config.set_active_record(record_id)
    click.echo(f"Active record: {record_id}")


@record_group.command("tree")
@click.option("--json", "json_output", is_flag=True)
@click.pass_obj
def record_tree(state: CLIContext, json_output: bool) -> None:
    """Show the logical parent tree."""
    records = list_records(config=state.config)
    if json_output:
        _emit_json([{"id": r.id, "parent_id": r.parent_id} for r in records])
        return
    children: dict[str | None, list[Record]] = {}
    for record in records:
        children.setdefault(record.parent_id, []).append(record)

    def visit(parent: str | None, depth: int) -> None:
        for child in children.get(parent, []):
            click.echo("  " * depth + child.id)
            visit(child.id, depth + 1)

    visit(None, 0)


@record_group.command("entrypoints")
@click.argument("record_id", required=False)
@click.option("--json", "json_output", is_flag=True)
@click.pass_obj
def record_entrypoints(
    state: CLIContext, record_id: str | None, json_output: bool
) -> None:
    """List effective entrypoints and their defining records."""
    resolved = _active(state.config, record_id).resolve(config=state.config)
    entries = resolved.values.get("entrypoints") or {}
    sources = resolved.sources.get("entrypoints") or {}
    if json_output:
        _emit_json({"entrypoints": entries, "sources": sources})
    else:
        click.echo(
            _table(
                ("name", "command", "source"),
                [
                    (
                        name,
                        shlex.join([*entry.command, *render_arguments(entry.args)]),
                        sources.get(name),
                    )
                    for name, entry in entries.items()
                ],
            )
        )


def _install_generic(
    name: str, cls: type[BaseRecord], loader: Any, lister: Any, directory: str
) -> None:
    @main.group(name=name)
    def group() -> None:
        """Manage generic Markdown records."""

    @group.command("create")
    @click.argument("identifier")
    @click.option("--set", "set_values", multiple=True)
    @click.option("--markdown-file", type=click.Path(path_type=Path))
    @click.pass_obj
    def create(
        state: CLIContext,
        identifier: str,
        set_values: tuple[str, ...],
        markdown_file: Path | None,
    ) -> None:
        validate_identifier(identifier)
        record = cls(
            id=identifier, markdown=_markdown(markdown_file), **_fields(set_values)
        )
        path = getattr(state.config, directory) / identifier / f"{name}.md"
        record.save(path)
        click.echo(f"Created {name} {identifier} at {path}")

    @group.command("list")
    @click.option("--json", "json_output", is_flag=True)
    @click.pass_obj
    def listing(state: CLIContext, json_output: bool) -> None:
        records = lister(config=state.config)
        _emit_json(records) if json_output else click.echo(
            _table(("id",), [(r.id,) for r in records])
        )

    @group.command("show")
    @click.argument("identifier")
    @click.option("--json", "json_output", is_flag=True)
    @click.pass_obj
    def show(state: CLIContext, identifier: str, json_output: bool) -> None:
        record = loader(identifier, config=state.config)
        _emit_json(record) if json_output else click.echo(
            f"---\n{dump_yaml(record.to_dict())}---\n{record.markdown}", nl=False
        )

    @group.command("update")
    @click.argument("identifier")
    @click.option("--set", "set_values", multiple=True)
    @click.option("--unset", "unset_values", multiple=True)
    @click.option("--markdown-file", type=click.Path(path_type=Path))
    @click.pass_obj
    def update(
        state: CLIContext,
        identifier: str,
        set_values: tuple[str, ...],
        unset_values: tuple[str, ...],
        markdown_file: Path | None,
    ) -> None:
        record = loader(identifier, config=state.config)
        _update_fields(record, set_values, unset_values, markdown_file)
        click.echo(f"Updated {name} {identifier}")

    @group.command("edit")
    @click.argument("identifier")
    @click.pass_obj
    def edit(state: CLIContext, identifier: str) -> None:
        path = getattr(state.config, directory) / identifier / f"{name}.md"
        _edit_recoverable(
            path,
            lambda: loader(identifier, config=state.config),
            f"{name} {identifier!r}",
        )
        click.echo(f"Validated {name} {identifier}")

    @group.command("delete")
    @click.argument("identifier")
    @click.pass_obj
    def delete(state: CLIContext, identifier: str) -> None:
        record = loader(identifier, config=state.config)
        record.delete()
        click.echo(f"Deleted {name} {identifier}")


_install_generic("program", Program, load_program, list_programs, "programs_dir")
_install_generic("context", Context, load_context, list_contexts, "contexts_dir")


@main.group("action")
def action_group() -> None:
    """Manage agent actions."""


@action_group.command("list")
@click.option("--entity")
@click.option("--json", "json_output", is_flag=True)
@click.pass_obj
def action_list(state: CLIContext, entity: str | None, json_output: bool) -> None:
    records = list_actions(entity=entity, config=state.config)
    _emit_json(records) if json_output else click.echo(
        _table(("entity", "id"), [(r.entity, r.id) for r in records])
    )


@action_group.command("show")
@click.argument("reference")
@click.option("--json", "json_output", is_flag=True)
@click.pass_obj
def action_show(state: CLIContext, reference: str, json_output: bool) -> None:
    record = _action_ref(reference, state.config)
    _emit_json(record) if json_output else click.echo(
        f"---\n{dump_yaml(record.to_dict())}---\n{record.markdown}", nl=False
    )


def _action_ref(reference: str, config: HirakuConfig) -> Action:
    if reference.count("/") != 1:
        raise ValidationError("ACTION must have the form entity/action-id")
    entity, identifier = reference.split("/", 1)
    return load_action(identifier, entity=entity, config=config)


@action_group.command("create")
@click.argument("reference")
@click.option("--set", "set_values", multiple=True)
@click.option("--markdown-file", type=click.Path(path_type=Path))
@click.pass_obj
def action_create(
    state: CLIContext,
    reference: str,
    set_values: tuple[str, ...],
    markdown_file: Path | None,
) -> None:
    if reference.count("/") != 1:
        raise ValidationError("ACTION must have the form entity/action-id")
    entity, identifier = reference.split("/", 1)
    validate_identifier(entity, label="action entity")
    validate_identifier(identifier, label="action ID")
    record = Action(
        id=identifier,
        entity=entity,
        markdown=_markdown(markdown_file),
        **_fields(set_values),
    )
    path = state.config.actions_dir / entity / identifier / "action.md"
    record.save(path)
    click.echo(f"Created action {reference} at {path}")


@action_group.command("update")
@click.argument("reference")
@click.option("--set", "set_values", multiple=True)
@click.option("--unset", "unset_values", multiple=True)
@click.option("--markdown-file", type=click.Path(path_type=Path))
@click.pass_obj
def action_update(
    state: CLIContext,
    reference: str,
    set_values: tuple[str, ...],
    unset_values: tuple[str, ...],
    markdown_file: Path | None,
) -> None:
    _update_fields(
        _action_ref(reference, state.config),
        set_values,
        unset_values,
        markdown_file,
        immutable={"id", "entity"},
    )
    click.echo(f"Updated action {reference}")


@action_group.command("edit")
@click.argument("reference")
@click.pass_obj
def action_edit(state: CLIContext, reference: str) -> None:
    if reference.count("/") != 1:
        raise ValidationError("ACTION must have the form entity/action-id")
    entity, identifier = reference.split("/", 1)
    path = state.config.actions_dir / entity / identifier / "action.md"
    _edit_recoverable(
        path,
        lambda: _action_ref(reference, state.config),
        f"Action {reference!r}",
    )
    click.echo(f"Validated action {reference}")


@action_group.command("delete")
@click.argument("reference")
@click.pass_obj
def action_delete(state: CLIContext, reference: str) -> None:
    _action_ref(reference, state.config).delete()
    click.echo(f"Deleted action {reference}")


@main.command("launch")
@click.argument("entrypoint_name")
@click.argument("entrypoint_overrides", nargs=-1, type=click.UNPROCESSED)
@click.option("--record", "record_id")
@click.option("--commit")
@click.option("--dispatch")
@click.option("--dry-run", is_flag=True)
@click.pass_obj
def launch_command(
    state: CLIContext,
    entrypoint_name: str,
    entrypoint_overrides: tuple[str, ...],
    record_id: str | None,
    commit: str | None,
    dispatch: str | None,
    dry_run: bool,
) -> None:
    """Launch an effective record entrypoint."""
    record = _active(state.config, record_id)
    entrypoint = record.entrypoint(entrypoint_name, config=state.config)
    overrides = _overrides(entrypoint_overrides)
    if dry_run:
        click.echo(f"Dispatcher: {_dispatcher(state.config, dispatch)}")
        arguments = dict(entrypoint.args)
        arguments.update(overrides)
        command = [*entrypoint.command, *render_arguments(arguments)]
        click.echo(f"Command: {shlex.join(command)}")
        return
    result = launch(
        entrypoint,
        name=entrypoint_name,
        args=overrides,
        record_id=record.id,
        commit=commit,
        dispatch=dispatch,
        check=False,
        config=state.config,
    )
    if result.returncode:
        raise click.exceptions.Exit(result.returncode)


@main.command("runs")
@click.argument("subcommand", required=False, type=click.Choice(["list"]))
@click.option("--record", "record_id")
@click.option("--descendants", is_flag=True)
@click.option("--include-unattributed", is_flag=True)
@click.option("--metrics")
@click.option("--params")
@click.option("--sort")
@click.option("--ascending", is_flag=True)
@click.option("--descending", is_flag=True)
@click.option("--limit", type=click.IntRange(min=0))
@click.option("--json", "json_output", is_flag=True)
@click.pass_obj
def run_list(
    state: CLIContext,
    subcommand: str | None,
    record_id: str | None,
    descendants: bool,
    include_unattributed: bool,
    metrics: str | None,
    params: str | None,
    sort: str | None,
    ascending: bool,
    descending: bool,
    limit: int | None,
    json_output: bool,
) -> None:
    """List runs from the selected record's tracking source."""
    if ascending and descending:
        raise ValidationError("--ascending and --descending are mutually exclusive")
    if (ascending or descending) and sort is None:
        raise ValidationError("A sort direction requires --sort")
    record = _active(state.config, record_id)
    declarations = record.resolve(config=state.config).values.get("metrics") or []
    metric_names = (
        _names(metrics, "metric")
        if metrics is not None
        else [item.name for item in declarations]
    )
    param_names = _names(params, "parameter")
    query = {
        key: value
        for key, value in {"sort": sort, "limit": limit}.items()
        if value is not None
    }
    if sort is not None:
        directions = {item.name: item.direction for item in declarations}
        query["ascending"] = (
            ascending if ascending or descending else directions.get(sort) != "maximize"
        )
    runs = record.runs(
        config=state.config,
        include_descendants=descendants,
        include_unattributed=include_unattributed,
        **query,
    )
    if json_output:
        _emit_json(runs)
    else:
        columns = ["id", "record", "timestamp", "commit"]
        columns.extend(f"param:{name}" for name in param_names)
        columns.extend(f"metric:{name}" for name in metric_names)
        rows = [
            [
                run.id,
                run.record_id,
                run.timestamp or "",
                run.commit or "",
                *(run.params.get(name) for name in param_names),
                *(run.metrics.get(name) for name in metric_names),
            ]
            for run in runs
        ]
        click.echo(_table(columns, rows))


if __name__ == "__main__":
    main()
