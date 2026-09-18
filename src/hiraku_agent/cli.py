"""`hiraku-agent`: the thin CLI over `schedule.suggest_next` and `gates.approve`.

Deliberately small. Everything else a research loop needs — creating a node,
binding a commit, launching, inspecting runs — is already `hiraku`'s own CLI
(`hiraku experiment ...`, `hiraku launch`, `hiraku run list`). This module
only adds what `hiraku` doesn't have: "what's next" and "approve this."
"""

from __future__ import annotations

import json as json_module
from typing import Any

import click

from hiraku.config import HirakuConfig
from hiraku.errors import HirakuError, NoActiveTaskError

from .gates import approve as approve_node
from .node import load_node
from .schedule import suggest_next


def _resolve_task(config: HirakuConfig, task_id: str | None) -> str:
    if task_id is not None:
        return task_id
    if config.active_task_id is not None:
        return config.active_task_id
    raise NoActiveTaskError(
        "No task given, and no active task is set (`hiraku task set <id>`)"
    )


@click.group(name="hiraku-agent")
def main() -> None:
    """The research-loop scheduler on top of `hiraku`."""


@main.command("next")
@click.option("task_id", "--task")
@click.option("json_output", "--json", is_flag=True)
def next_command(task_id: str | None, json_output: bool) -> None:
    """Print the single next action for a task, and why."""
    config = HirakuConfig.discover()
    try:
        resolved_task = _resolve_task(config, task_id)
        suggestion: dict[str, Any] = suggest_next(resolved_task, config=config)
    except HirakuError as exc:
        raise click.ClickException(str(exc)) from exc
    if json_output:
        click.echo(json_module.dumps(suggestion, indent=2, sort_keys=True))
        return
    click.echo(f"{suggestion['action']}: {suggestion['why']}")
    if "node" in suggestion:
        click.echo(f"  node: {resolved_task}:{suggestion['node']}")
    for command in suggestion.get("next_commands", []):
        click.echo(f"  {command}")


@main.command("approve")
@click.argument("node_id")
@click.option("task_id", "--task")
@click.option("--by", required=True, help="who is approving this")
def approve_command(node_id: str, task_id: str | None, by: str) -> None:
    """Clear the approval gate on a proposed node (a no-op in `auto` mode)."""
    config = HirakuConfig.discover()
    try:
        resolved_task = _resolve_task(config, task_id)
        node = load_node(node_id, task=resolved_task, config=config)
        approve_node(node, by=by)
    except HirakuError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Approved {resolved_task}:{node_id} (by {by})")
