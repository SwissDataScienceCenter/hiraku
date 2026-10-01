"""`hiraku-agent`: the thin CLI over the scheduler and the LLM-driven workflows.

Deliberately small where `hiraku` already covers something. Creating a node, binding a
commit by hand, launching, inspecting runs — all `hiraku`'s own CLI (`hiraku experiment
...`, `hiraku launch`, `hiraku run list`). This module adds what `hiraku` doesn't have:
"what's next", "approve this," the six LLM-driven workflows (`draft`, `design`,
`discover`, `implement`, `triage`, `analyze`) described in `workflows.py`, and `loop`,
the bounded driver over `loop.py` for a saved `Program` record. `implement` is the one
workflow among those six that writes real files and a real commit rather than only a
`Node` record — see `workflows.run_implement`'s docstring.
"""

from __future__ import annotations

import json as json_module
from typing import Any

import click

from hiraku.config import HirakuConfig
from hiraku.errors import HirakuError, NoActiveTaskError

from .engine import AgentRun, Hooks
from .engine import agent_command as resolve_agent_cmd
from .gates import approve as approve_node
from .loop import LoopResult, run_loop
from .node import load_node
from .schedule import suggest_next
from .workflows import (
    run_analyze,
    run_design,
    run_discover,
    run_draft,
    run_implement,
    run_triage,
)


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


# ---------------------------------------------------------------------- agent workflows

_AGENT_OPTIONS = (
    click.option(
        "instruction", "--instruction", "-m", default="", help="steer the agent"
    ),
    click.option(
        "instruction_file",
        "--instruction-file",
        type=click.Path(exists=True, dir_okay=False),
        default=None,
        help="read --instruction from a file instead",
    ),
    click.option(
        "agent_cmd", "--agent-cmd", default=None, help="override the agent command"
    ),
    click.option(
        "dry_run", "--dry-run", is_flag=True, help="print the prompt, call nothing"
    ),
    click.option(
        "no_apply", "--no-apply", is_flag=True, help="call the agent, but write nothing"
    ),
    click.option(
        "context_paths", "--context", multiple=True, help="a file to read as background"
    ),
    click.option(
        "action_id",
        "--action",
        default=None,
        help="load agent-cmd/timeout/context defaults from a saved Action record",
    ),
    click.option(
        "context_id",
        "--context-id",
        default=None,
        help="add a saved Context record's files as background",
    ),
    click.option("timeout", "--timeout", type=int, default=None),
    click.option(
        "unset_env",
        "--unset-env",
        multiple=True,
        help="drop this env var before calling",
    ),
    click.option(
        "json_output", "--json", is_flag=True, help="print the agent's raw JSON reply"
    ),
)


def _apply_agent_options(func: Any) -> Any:
    for option in reversed(_AGENT_OPTIONS):
        func = option(func)
    return func


def _resolve_instruction(instruction: str, instruction_file: str | None) -> str:
    if instruction_file:
        from pathlib import Path

        return Path(instruction_file).read_text(encoding="utf-8")
    return instruction


def _hooks() -> Hooks:
    """Progress noise on stderr — an agent call is minutes long and silent otherwise."""

    def start(info: dict[str, Any]) -> None:
        click.echo(f"calling {info['command']!r} ...", err=True)

    last = {"tick": -30.0}

    def tick(elapsed: float) -> None:
        if elapsed - last["tick"] >= 15:
            last["tick"] = elapsed
            click.echo(f"  ... still running ({elapsed:.0f}s)", err=True)

    def done(info: dict[str, Any]) -> None:
        if info.get("error"):
            click.echo(f"failed after {info['seconds']}s: {info['error']}", err=True)
        else:
            click.echo(f"done in {info['seconds']}s (log: {info['log']})", err=True)

    return Hooks(start=start, tick=tick, done=done)


def _run_agent_command(
    runner: Any,
    *args: Any,
    json_output: bool,
    no_apply: bool,
    dry_run: bool,
    **workflow_kwargs: Any,
) -> None:
    """Call one workflow function and print its result.

    `json_output`/`no_apply`/`dry_run` are CLI-presentation concerns, kept out of
    `workflow_kwargs` so it can be splatted straight into the workflow function's own
    signature (`run_design`, `run_discover`, `run_triage`, `run_analyze` all take
    `dry_run`/`apply`/... directly, not a `json_output`/`no_apply` pair).
    """
    config = HirakuConfig.discover()
    try:
        result: AgentRun = runner(
            *args, config=config, dry_run=dry_run, **workflow_kwargs
        )
    except HirakuError as exc:
        raise click.ClickException(str(exc)) from exc
    if dry_run:
        click.echo(result.prompt)
        return
    if json_output:
        click.echo(
            json_module.dumps(result.data, indent=2, sort_keys=True, default=str)
        )
    else:
        for note_key in ("notes", "missing_evidence", "blocked"):
            if result.data.get(note_key):
                click.echo(f"{note_key}: {result.data[note_key]}")
        if result.written:
            click.echo("wrote:")
            for path in result.written:
                click.echo(f"  {path}")
        else:
            click.echo("wrote nothing" + (" (--no-apply)" if no_apply else ""))


@main.command("draft")
@click.option("task_id", "--task-id", default=None, help="id for the new task")
@click.option("--branch", default=None, help="git branch (default: the id, per config)")
@click.option(
    "no_activate", "--no-activate", is_flag=True, help="don't make the new task active"
)
@_apply_agent_options
def draft_command(
    task_id: str | None,
    branch: str | None,
    no_activate: bool,
    instruction: str,
    instruction_file: str | None,
    agent_cmd: str | None,
    dry_run: bool,
    no_apply: bool,
    context_paths: tuple[str, ...],
    action_id: str | None,
    context_id: str | None,
    timeout: int | None,
    unset_env: tuple[str, ...],
    json_output: bool,
) -> None:
    """Turn free-text instruction into a new task — the same effect as `hiraku task
    create`."""
    _run_agent_command(
        run_draft,
        _resolve_instruction(instruction, instruction_file),
        task_id=task_id,
        branch=branch,
        activate=not no_activate,
        command=resolve_agent_cmd(agent_cmd) if agent_cmd else None,
        dry_run=dry_run,
        apply=not no_apply,
        context_paths=list(context_paths),
        action=action_id,
        context_id=context_id,
        unset_env=list(unset_env),
        hooks=None if dry_run else _hooks(),
        timeout=timeout,
        json_output=json_output,
        no_apply=no_apply,
    )


@main.command("design")
@click.option("task_id", "--task", default=None)
@_apply_agent_options
def design_command(
    task_id: str | None,
    instruction: str,
    instruction_file: str | None,
    agent_cmd: str | None,
    dry_run: bool,
    no_apply: bool,
    context_paths: tuple[str, ...],
    action_id: str | None,
    context_id: str | None,
    timeout: int | None,
    unset_env: tuple[str, ...],
    json_output: bool,
) -> None:
    """Propose a tree of experiments for a task."""
    config = HirakuConfig.discover()
    resolved_task = _resolve_task(config, task_id)
    _run_agent_command(
        run_design,
        resolved_task,
        instruction=_resolve_instruction(instruction, instruction_file),
        command=resolve_agent_cmd(agent_cmd) if agent_cmd else None,
        dry_run=dry_run,
        apply=not no_apply,
        context_paths=list(context_paths),
        action=action_id,
        context_id=context_id,
        unset_env=list(unset_env),
        hooks=None if dry_run else _hooks(),
        timeout=timeout,
        json_output=json_output,
        no_apply=no_apply,
    )


@main.command("discover")
@click.option("task_id", "--task", default=None)
@_apply_agent_options
def discover_command(
    task_id: str | None,
    instruction: str,
    instruction_file: str | None,
    agent_cmd: str | None,
    dry_run: bool,
    no_apply: bool,
    context_paths: tuple[str, ...],
    action_id: str | None,
    context_id: str | None,
    timeout: int | None,
    unset_env: tuple[str, ...],
    json_output: bool,
) -> None:
    """Search for candidate hypotheses for a task, from the literature or an idea you
    give it."""
    config = HirakuConfig.discover()
    resolved_task = _resolve_task(config, task_id)
    _run_agent_command(
        run_discover,
        resolved_task,
        instruction=_resolve_instruction(instruction, instruction_file),
        command=resolve_agent_cmd(agent_cmd) if agent_cmd else None,
        dry_run=dry_run,
        apply=not no_apply,
        context_paths=list(context_paths),
        action=action_id,
        context_id=context_id,
        unset_env=list(unset_env),
        hooks=None if dry_run else _hooks(),
        timeout=timeout,
        json_output=json_output,
        no_apply=no_apply,
    )


@main.command("implement")
@click.argument("node_id")
@click.option("task_id", "--task", default=None)
@_apply_agent_options
def implement_command(
    node_id: str,
    task_id: str | None,
    instruction: str,
    instruction_file: str | None,
    agent_cmd: str | None,
    dry_run: bool,
    no_apply: bool,
    context_paths: tuple[str, ...],
    action_id: str | None,
    context_id: str | None,
    timeout: int | None,
    unset_env: tuple[str, ...],
    json_output: bool,
) -> None:
    """Write NODE_ID's approved proposal as real code, and bind the resulting commit.

    NODE_ID must already be approved (`hiraku-agent approve`) — in guided mode that
    approval is the human trigger; the agent is what writes the code either way. Unlike
    every other workflow here, the agent is given file-write and shell tools (see
    `workflows.run_implement`'s docstring), so review what it wrote with `git diff`/`git
    log` on the commit it reports before trusting it.
    """
    _run_agent_command(
        run_implement,
        node_id,
        task_id=task_id,
        instruction=_resolve_instruction(instruction, instruction_file),
        command=resolve_agent_cmd(agent_cmd) if agent_cmd else None,
        dry_run=dry_run,
        apply=not no_apply,
        context_paths=list(context_paths),
        action=action_id,
        context_id=context_id,
        unset_env=list(unset_env),
        hooks=None if dry_run else _hooks(),
        timeout=timeout,
        json_output=json_output,
        no_apply=no_apply,
    )


@main.command("triage")
@click.argument("node_id")
@click.option("task_id", "--task", default=None)
@_apply_agent_options
def triage_command(
    node_id: str,
    task_id: str | None,
    instruction: str,
    instruction_file: str | None,
    agent_cmd: str | None,
    dry_run: bool,
    no_apply: bool,
    context_paths: tuple[str, ...],
    action_id: str | None,
    context_id: str | None,
    timeout: int | None,
    unset_env: tuple[str, ...],
    json_output: bool,
) -> None:
    """Classify why NODE_ID's result missed its expectation, and decide whether to
    follow up."""
    _run_agent_command(
        run_triage,
        node_id,
        task_id=task_id,
        instruction=_resolve_instruction(instruction, instruction_file),
        command=resolve_agent_cmd(agent_cmd) if agent_cmd else None,
        dry_run=dry_run,
        apply=not no_apply,
        context_paths=list(context_paths),
        action=action_id,
        context_id=context_id,
        unset_env=list(unset_env),
        hooks=None if dry_run else _hooks(),
        timeout=timeout,
        json_output=json_output,
        no_apply=no_apply,
    )


@main.command("analyze")
@click.argument("node_id")
@click.option("task_id", "--task", default=None)
@_apply_agent_options
def analyze_command(
    node_id: str,
    task_id: str | None,
    instruction: str,
    instruction_file: str | None,
    agent_cmd: str | None,
    dry_run: bool,
    no_apply: bool,
    context_paths: tuple[str, ...],
    action_id: str | None,
    context_id: str | None,
    timeout: int | None,
    unset_env: tuple[str, ...],
    json_output: bool,
) -> None:
    """Judge NODE_ID's runs against its expectation and write its findings."""
    _run_agent_command(
        run_analyze,
        node_id,
        task_id=task_id,
        instruction=_resolve_instruction(instruction, instruction_file),
        command=resolve_agent_cmd(agent_cmd) if agent_cmd else None,
        dry_run=dry_run,
        apply=not no_apply,
        context_paths=list(context_paths),
        action=action_id,
        context_id=context_id,
        unset_env=list(unset_env),
        hooks=None if dry_run else _hooks(),
        timeout=timeout,
        json_output=json_output,
        no_apply=no_apply,
    )


# -------------------------------------------------------------------------------- loop


@main.command("loop")
@click.option("program_id", "--program", required=True, help="Program record to drive")
@click.option("task_id", "--task", required=True, help="task to loop over")
@click.option(
    "max_steps",
    "--max-steps",
    type=int,
    default=None,
    help="hard cap on loop iterations (overrides the program's own budget fields)",
)
@click.option(
    "json_output", "--json", is_flag=True, help="print the full step-by-step result"
)
def loop_command(
    program_id: str, task_id: str, max_steps: int | None, json_output: bool
) -> None:
    """Drive a bounded research loop for TASK_ID, using PROGRAM_ID's bound actions.

    Repeats `hiraku-agent next`'s own suggestion, calling whichever `Action` the
    program binds to that workflow, until a budget is hit, a step makes no progress,
    the next suggestion needs a person or a real command (`approve`/`launch`), or
    there is nothing left to schedule. `implement` is no longer in that stopping set —
    see `loop.LLM_WORKFLOWS`'s comment for why driving it automatically is still safe.
    """
    config = HirakuConfig.discover()
    try:
        result: LoopResult = run_loop(
            program_id,
            task_id,
            max_steps=max_steps,
            hooks=_hooks(),
            config=config,
        )
    except HirakuError as exc:
        raise click.ClickException(str(exc)) from exc
    if json_output:
        click.echo(
            json_module.dumps(
                {
                    "steps": [
                        {
                            "action": step.action,
                            "action_id": step.action_id,
                            "node": step.node,
                            "why": step.why,
                            "written": [str(path) for path in step.written],
                        }
                        for step in result.steps
                    ],
                    "stopped": result.stopped,
                },
                indent=2,
            )
        )
        return
    for step in result.steps:
        target = f" {step.node}" if step.node else ""
        click.echo(f"{step.action}{target} (action: {step.action_id}) — {step.why}")
        for path in step.written:
            click.echo(f"  wrote: {path}")
    click.echo(f"stopped: {result.stopped}")
