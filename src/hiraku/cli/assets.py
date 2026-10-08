"""Click commands for Hiraku agent assets."""

from __future__ import annotations

from pathlib import Path

import click

from ..core.agent import Action, list_actions, load_action
from ..core.config import HirakuConfig
from ..core.errors import ValidationError
from ..core.io import dump_yaml
from ..core.validation import validate_identifier
from .cli import (
    CLIContext,
    _edit_recoverable,
    _emit_json,
    _fields,
    _markdown,
    _table,
    _update_fields,
    main,
)


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
