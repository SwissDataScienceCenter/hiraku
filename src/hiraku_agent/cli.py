"""`hiraku-agent`: the thin CLI over the scheduler and the LLM-driven workflows.

Deliberately small where `hiraku` already covers something. Creating a node, binding a
commit by hand, launching, inspecting runs — all `hiraku`'s own CLI (`hiraku record
...`, `hiraku launch`, `hiraku runs`). This module adds what `hiraku` doesn't have:
"what's next", "approve this," the six LLM-driven workflows (`draft`, `design`,
`discover`, `implement`, `triage`, `analyze`) described in `workflows.py`, and `loop`,
the bounded driver over `loop.py` for a saved `Program` record. `implement` is the one
workflow among those six that writes real files and a real commit rather than only a
`Node` record — see `workflows.run_implement`'s docstring.
"""

from __future__ import annotations

import json as json_module
import os
import shlex
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

import click

from hiraku.config import HirakuConfig
from hiraku.errors import HirakuError, NoActiveRecordError
from hiraku.record import list_records, load_record

from .cleanup import delete_subtree, describe, plan_delete, refusal
from .defaults import (
    defaults_path,
    load_defaults,
    merge_defaults,
    save_defaults,
)
from .dispatch_setup import (
    CHECK_RECORD_ID,
    check_dispatch,
    describe_check,
    run_setup_dispatch,
    set_default_dispatch,
)
from .dispatch_setup import to_json as dispatch_json
from .engine import DEFAULT_IMPLEMENT_TIMEOUT, AgentRun, Hooks, stop_all_agents
from .engine import agent_command as resolve_agent_cmd
from .errors import AgentError
from .flows.bind import BindPlan, run_bind
from .gates import approve as approve_node
from .gates import is_approved
from .launches import mark_done, mark_running, running_nodes
from .loop import LoopFailed, LoopResult, run_loop
from .node import list_nodes, load_node, task_of
from .sandbox import OPT_OUT_ENV as SANDBOX_OPT_OUT_ENV
from .sandbox import missing_requirements
from .schedule import suggest_next
from .state import node_bookkeeping
from .workflows import (
    run_analyze,
    run_design,
    run_discover,
    run_draft,
    run_implement,
    run_plan,
    run_revise,
    run_triage,
    run_update,
)
from .worktree import resume_state


def _resolve_task(config: HirakuConfig, task_id: str | None) -> str:
    if task_id is not None:
        return task_id
    if config.active_record_id is not None:
        return config.active_record_id
    raise NoActiveRecordError(
        "No task given, and no active record is set (`hiraku record set <id>`)"
    )


SHOW_STEPS_ENV = "HIRAKU_AGENT_SHOW_STEPS"


@click.group(name="hiraku-agent")
@click.option(
    "no_sandbox",
    "--no-sandbox",
    is_flag=True,
    help="turn the project's write sandbox off for this command",
)
@click.option(
    "show_steps",
    "--steps",
    is_flag=True,
    help="print every agent step (each command, file read or edit) while it runs, "
    "not only its progress notes",
)
def main(no_sandbox: bool, show_steps: bool) -> None:
    """The research-loop scheduler on top of `hiraku`."""
    if no_sandbox:
        os.environ[SANDBOX_OPT_OUT_ENV] = "1"
    if show_steps:
        os.environ[SHOW_STEPS_ENV] = "1"


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
        click.echo(f"  node: {suggestion['node']}")
    attempt = suggestion.get("last_attempt")
    if attempt:
        click.echo(
            f"  last attempt {attempt['outcome']} ({attempt.get('started') or '?'}): "
            f"{attempt['reason']}\n  log: {attempt['log']}"
        )
    for command in suggestion.get("next_commands", []):
        click.echo(f"  {command}")


@main.command("approve")
@click.argument("node_ids", nargs=-1)
@click.option("task_id", "--task")
@click.option("--by", required=True, help="who is approving this")
@click.option(
    "approve_all",
    "--all",
    is_flag=True,
    help="every node of the task still waiting for approval",
)
@click.option("yes", "--yes", "-y", is_flag=True, help="with --all: don't ask first")
def approve_command(
    node_ids: tuple[str, ...],
    task_id: str | None,
    by: str,
    approve_all: bool,
    yes: bool,
) -> None:
    """Clear the approval gate on one or more nodes (a no-op in `auto` mode).

    Name the nodes (`approve a b c --by you`), or pass --all to approve every node of
    the task still waiting for approval: proposed nodes, and implemented ones whose
    approval an update cleared. --all lists them with their expectations and asks
    before approving. Abandoned nodes are never approved."""
    if approve_all == bool(node_ids):
        raise click.UsageError("Name the nodes to approve, or pass --all (not both).")
    config = HirakuConfig.discover()
    try:
        resolved_task = _resolve_task(config, task_id)
        if approve_all:
            nodes = _waiting_for_approval(resolved_task, config)
            if not nodes:
                click.echo(f"Nothing in {resolved_task} is waiting for approval.")
                return
            click.echo(f"{len(nodes)} node(s) in {resolved_task} waiting for approval:")
            for node, state in nodes:
                click.echo(
                    f"  {node.id} [{state}] {node.expectation or '(no expectation)'}"
                )
            if not yes:
                click.confirm(f"Approve all {len(nodes)} as {by}?", abort=True)
            targets = [node for node, _ in nodes]
        else:
            targets = [
                load_node(node_id, task=resolved_task, config=config)
                for node_id in node_ids
            ]
            abandoned = [node.id for node in targets if node.abandoned]
            if abandoned:
                raise AgentError(
                    f"Not approving abandoned node(s): {', '.join(abandoned)}. "
                    "Nothing was approved."
                )
        for node in targets:
            already = node.mode == "auto" or node.approved_by is not None
            approve_node(node, by=by)
            note = " (already approved)" if already else ""
            click.echo(f"Approved {resolved_task}:{node.id} (by {by}){note}")
    except HirakuError as exc:
        raise click.ClickException(str(exc)) from exc


def _waiting_for_approval(task_id: str, config: HirakuConfig) -> list[tuple[Any, str]]:
    """Nodes of `task_id` that the approval gate is still holding back."""
    nodes = list_nodes(task=task_id, config=config)
    _, states, _ = node_bookkeeping(nodes, config=config)
    return [
        (node, states[node.id])
        for node in nodes
        if not node.abandoned
        and not is_approved(node)
        and states.get(node.id) in {"proposed", "implemented"}
    ]


def _describe(marker: dict[str, Any]) -> str:
    if marker.get("source") == "tracking":
        return f"run {marker.get('run')} reports {marker.get('status')}"
    parts = []
    if marker.get("jobs"):
        parts.append("Slurm job " + ", ".join(str(j) for j in marker["jobs"]))
    if marker.get("check"):
        parts.append("check " + shlex.join(str(c) for c in marker["check"]))
    if marker.get("pid") is not None:
        parts.append(f"pid {marker['pid']} on {marker.get('host')}")
    return "; ".join(parts) or "marker"


def _runs_by_record(config: HirakuConfig) -> dict[str, list[Any]]:
    """Every attributed run in the project's tracking source, grouped by record id."""
    records = list_records(config=config)
    grouped: dict[str, list[Any]] = {}
    if records:
        for run in records[0].runs(config=config):
            grouped.setdefault(run.record_id, []).append(run)
    return grouped


@main.command("delete")
@click.argument("record_id")
@click.option("yes", "--yes", "-y", is_flag=True, help="don't ask for confirmation")
@click.option(
    "force",
    "--force",
    is_flag=True,
    help="also delete records that have runs or a commit",
)
@click.option("dry_run", "--dry-run", is_flag=True, help="only list what would go")
def delete_command(record_id: str, yes: bool, force: bool, dry_run: bool) -> None:
    """Delete RECORD_ID and everything beneath it (a whole task, or one node's subtree).

    Lists what will be removed and asks first. Records with runs or a commit are kept
    unless --force; runs stay in the tracking source and commits in Git either way.
    The active record is cleared if it was inside; Git branches are listed, never
    deleted."""
    config = HirakuConfig.discover()
    try:
        plan = plan_delete(record_id, config=config)
    except HirakuError as exc:
        raise click.ClickException(str(exc)) from exc
    for line in describe(plan):
        click.echo(line)
    if dry_run:
        return
    reason = refusal(plan, force=force)
    if reason:
        raise click.ClickException(reason)
    if not yes:
        click.confirm(f"Delete these {len(plan.records)} record(s)?", abort=True)
    try:
        deleted = delete_subtree(plan, force=force, config=config)
    except HirakuError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"deleted {len(deleted)} record(s): {', '.join(deleted)}")


@main.command("mark-running")
@click.argument("node_id")
@click.option(
    "job_ids",
    "--job-id",
    multiple=True,
    help="a Slurm job id of this node (repeat for several; `sbatch --parsable` output)",
)
@click.option(
    "pid", "--pid", type=int, default=None, help="a foreground process on this host"
)
@click.option(
    "check",
    "--check",
    default=None,
    help="a command that exits 0 while the work is alive (any scheduler or runtime)",
)
def mark_running_command(
    node_id: str, job_ids: tuple[str, ...], pid: int | None, check: str | None
) -> None:
    """Record that a node has a launch in flight, so it reads as `running`.

    Call it from the script that submits the job, right after `sbatch`:

        jid=$(sbatch --parsable job.sbatch ...)
        hiraku-agent mark-running "$HIRAKU_RECORD_ID" --job-id "$jid"

    The node stays `running` while any of its jobs is still known to Slurm; no clean-up
    call is needed.
    """
    config = HirakuConfig.discover()
    try:
        # `check-dispatch` probes a dispatcher under a reserved id with no record.
        if node_id != CHECK_RECORD_ID:
            load_record(node_id, config=config)
        marker = mark_running(
            node_id,
            jobs=job_ids,
            pid=pid,
            check=shlex.split(check) if check else (),
            config=config,
        )
    except (HirakuError, AgentError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"{node_id}: running ({_describe(marker)})")


@main.command("mark-done")
@click.argument("node_id")
def mark_done_command(node_id: str) -> None:
    """Clear a node's in-flight marker (for a launch that was killed or cancelled)."""
    config = HirakuConfig.discover()
    removed = mark_done(node_id, config=config)
    click.echo(f"{node_id}: marker removed" if removed else f"{node_id}: no marker")


@main.command("running")
@click.argument("node_id", required=False)
@click.option("json_output", "--json", is_flag=True)
def running_command(node_id: str | None, json_output: bool) -> None:
    """List the launches in flight. With NODE_ID, exit 0 if it is running, else 1.

    Handy at the top of a dispatcher: `hiraku-agent running "$HIRAKU_RECORD_ID" &&
    exit 1` refuses a second submission of the same node.
    """
    config = HirakuConfig.discover()
    found = running_nodes(_runs_by_record(config), config)
    if node_id is not None:
        if node_id in found and not json_output:
            click.echo(f"{node_id}: running")
        if json_output:
            click.echo(json_module.dumps(found.get(node_id), indent=2))
        raise click.exceptions.Exit(0 if node_id in found else 1)
    if json_output:
        click.echo(json_module.dumps(found, indent=2))
        return
    if not found:
        click.echo("no launches in flight")
    for record_id, marker in found.items():
        click.echo(
            f"{record_id}\t{_describe(marker)}\tsince {marker.get('started_at')}"
        )


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
    click.option(
        "interactive",
        "--interactive",
        is_flag=True,
        help=(
            "talk to the agent on this terminal instead of one call: it writes its "
            "reply when you tell it to finalise (needs a terminal)"
        ),
    ),
)


def _apply_agent_options(func: Any) -> Any:
    for option in reversed(_AGENT_OPTIONS):
        func = option(func)
    return func


def _repo_option(func: Any) -> Any:
    """`--repo PATH` — on every agent command except `implement`, which already edits
    files in this project and should not be handed write-capable reach into another."""
    return click.option(
        "repo_paths",
        "--repo",
        multiple=True,
        type=click.Path(exists=True, file_okay=False),
        help="a repository the agent may read (read-only); repeatable",
    )(func)


def _resolve_instruction(instruction: str, instruction_file: str | None) -> str:
    if instruction_file:
        return Path(instruction_file).read_text(encoding="utf-8")
    return instruction


def _hooks(prefix: str | None = None) -> Hooks:
    """Progress noise on stderr — an agent call is minutes long and silent otherwise.

    `prefix` (the node id, for `implement --jobs`) starts every line, so the output
    of calls running side by side can be told apart.
    """
    tag = f"[{prefix}] " if prefix else ""

    def start(info: dict[str, Any]) -> None:
        last["started"] = time.monotonic()
        last["printed"] = 0.0
        click.echo(f"{tag}calling {info['command']!r} ...", err=True)
        sandbox = info.get("sandbox")
        if sandbox and sandbox.get("enabled"):
            click.echo(
                f"  {tag}sandbox on: no writes to "
                + (", ".join(sandbox["deny_write"]) or "(nothing listed)")
                + " (writes only in the working directory, temp"
                + (
                    ", " + ", ".join(sandbox["allow_write"])
                    if sandbox["allow_write"]
                    else ""
                )
                + ")",
                err=True,
            )
        elif sandbox:
            click.echo(
                f"  {tag}WARNING: sandbox OFF for this call ({sandbox['reason']})",
                err=True,
            )

    # By default only the agent's own progress notes are printed, plus a heartbeat
    # naming its latest step; `--steps` prints every step. All of them are in the
    # call's transcript either way.
    verbose = bool(os.environ.get(SHOW_STEPS_ENV))
    last: dict[str, Any] = {
        "printed": 0.0,
        "started": time.monotonic(),
        "steps": 0,
        "step": "",
    }

    def stamp() -> str:
        minutes, seconds = divmod(int(time.monotonic() - last["started"]), 60)
        who = f"{prefix} " if prefix else ""
        return f"[{who}{minutes:02d}:{seconds:02d}]"

    def say(line: str) -> None:
        last["printed"] = time.monotonic()
        click.echo(f"  {stamp()} {line}", err=True)

    def tick(elapsed: float) -> None:
        quiet_for = time.monotonic() - (last["printed"] or last["started"])
        if quiet_for < (30 if verbose else 60):
            return
        detail = ""
        if last["steps"] and not verbose:
            detail = f", {last['steps']} steps, last: {last['step']}"
        say(f"... still running{detail}")

    def step(line: str) -> None:
        last["steps"] += 1
        last["step"] = line if len(line) <= 100 else line[:99] + "…"
        if verbose:
            say(line)

    def note(line: str) -> None:
        say(f"progress: {line}")

    def done(info: dict[str, Any]) -> None:
        if info.get("error"):
            error = str(info["error"])
            if prefix:  # the full message comes with the summary
                error = error.splitlines()[0] if error else error
            click.echo(f"{tag}failed after {info['seconds']}s: {error}", err=True)
        else:
            click.echo(
                f"{tag}done in {info['seconds']}s (log: {info['log']})", err=True
            )

    return Hooks(start=start, tick=tick, done=done, step=step, note=note)


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
        workflow_kwargs, notices = merge_defaults(workflow_kwargs, config)
    except HirakuError as exc:
        raise click.ClickException(str(exc)) from exc
    for notice in notices:
        click.echo(f"defaults: {notice}", err=True)
    try:
        result: AgentRun = runner(
            *args, config=config, dry_run=dry_run, **workflow_kwargs
        )
    except HirakuError as exc:
        raise click.ClickException(str(exc)) from exc
    if dry_run:
        click.echo(result.prompt)
        return
    _print_result(result, json_output=json_output, no_apply=no_apply)


def _print_result(result: AgentRun, *, json_output: bool, no_apply: bool) -> None:
    """Print what one workflow call did."""
    if json_output:
        click.echo(
            json_module.dumps(result.data, indent=2, sort_keys=True, default=str)
        )
    else:
        if result.data.get("approvals_cleared"):
            click.echo(
                "approval cleared (re-approve): "
                + ", ".join(result.data["approvals_cleared"])
            )
        if result.data.get("skipped"):
            click.echo("skipped: " + "; ".join(result.data["skipped"]))
        if result.data.get("approval_cleared"):
            click.echo("approval cleared: the expectation changed, so re-approve it")
        if result.data.get("injection_warning"):
            click.echo(
                "WARNING: a source the agent read tried to instruct it. Every record "
                "this call wrote has an `Injection warning` section, and its proposals "
                "need a person's approval; read them before approving anything.",
                err=True,
            )
        if result.data.get("worktree_kept"):
            click.echo(
                f"the agent left uncommitted changes in {result.data['worktree_kept']}"
            )
        if result.data.get("investigation_written_to"):
            click.echo(
                f"investigation recorded in {result.data['investigation_written_to']}"
            )
        if result.data.get("changed"):
            click.echo("changed: " + ", ".join(result.data["changed"]))
        if result.data.get("unbound"):
            click.echo(
                "back to proposed (commit unbound, re-implement after approval): "
                + ", ".join(result.data["unbound"])
            )
        if result.data.get("synced"):
            click.echo("experiments re-synced: " + ", ".join(result.data["synced"]))
        if result.data.get("ignored"):
            click.echo(
                "ignored (frozen once experiments exist): "
                + ", ".join(result.data["ignored"])
            )
        for note_key in (
            "notes",
            "inputs_note",
            "missing_evidence",
            "blocked",
            "next",
            "hint",
        ):
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
@_repo_option
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
    repo_paths: tuple[str, ...],
    action_id: str | None,
    context_id: str | None,
    timeout: int | None,
    unset_env: tuple[str, ...],
    json_output: bool,
    interactive: bool,
) -> None:
    """Turn free-text instruction into a new task — the same effect as `hiraku record
    create ... --type task`."""
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
        repo_paths=list(repo_paths),
        action=action_id,
        context_id=context_id,
        unset_env=list(unset_env),
        interactive=interactive,
        hooks=None if dry_run or interactive else _hooks(),
        timeout=timeout,
        json_output=json_output,
        no_apply=no_apply,
    )


@main.command("setup-dispatch")
@click.option(
    "name", "--name", default=None, help="dispatcher file to write (e.g. slurm.sh)"
)
@click.option("force", "--force", is_flag=True, help="replace existing files")
@_apply_agent_options
@_repo_option
def setup_dispatch_command(
    name: str | None,
    force: bool,
    instruction: str,
    instruction_file: str | None,
    agent_cmd: str | None,
    dry_run: bool,
    no_apply: bool,
    context_paths: tuple[str, ...],
    repo_paths: tuple[str, ...],
    action_id: str | None,
    context_id: str | None,
    timeout: int | None,
    unset_env: list[str],
    json_output: bool,
    interactive: bool,
) -> None:
    """Choose and configure this project's dispatcher with the agent.

    It looks at the tools on this machine (sbatch, ray, kubectl, sky, ...), at how the
    project and its default repositories already launch work (.sbatch files, cluster
    configs, job manifests) and at -m, then copies a template (worktree.sh, slurm.sh)
    with its site settings, or writes a dispatcher for a backend with no template.
    The project default is not changed: run `check-dispatch NAME --set-default`."""
    _run_agent_command(
        run_setup_dispatch,
        _resolve_instruction(instruction, instruction_file),
        name=name,
        force=force,
        command=resolve_agent_cmd(agent_cmd) if agent_cmd else None,
        dry_run=dry_run,
        apply=not no_apply,
        repo_paths=list(repo_paths),
        context_paths=list(context_paths),
        context_id=context_id,
        action=action_id,
        unset_env=list(unset_env),
        interactive=interactive,
        hooks=None if dry_run or interactive else _hooks(),
        timeout=timeout,
        json_output=json_output,
        no_apply=no_apply,
    )


@main.command("check-dispatch")
@click.argument("name", required=False)
@click.option(
    "wait",
    "--wait",
    type=float,
    default=120.0,
    help="seconds to wait for a submitted job to run the probe",
)
@click.option(
    "set_default",
    "--set-default",
    is_flag=True,
    help="make it the project's default_dispatch if the check passes",
)
@click.option("json_output", "--json", is_flag=True)
def check_dispatch_command(
    name: str | None, wait: float, set_default: bool, json_output: bool
) -> None:
    """Check that dispatcher NAME (default: the project's) keeps the launch contract.

    Sends a probe through it asking for an older commit than the one checked out, and
    checks that the probe ran that commit, saw HIRAKU_COMMIT/HIRAKU_GIT_STATE and the
    record id, and, for a dispatcher that submits a job, that it printed the job id and
    recorded it with mark-running. No record is touched."""
    config = HirakuConfig.discover()
    target = name or config.default_dispatch
    try:
        result = check_dispatch(target, wait=wait, config=config)
    except HirakuError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(dispatch_json(result) if json_output else describe_check(result))
    if set_default:
        if result.status != "pass":
            raise click.ClickException(
                f"not setting {result.dispatcher} as the default: the check did not "
                "pass"
            )
        set_default_dispatch(result.dispatcher, config=config)
        click.echo(f"default_dispatch is now {result.dispatcher}")
    if result.status == "fail":
        raise SystemExit(1)
    if result.status == "pending":
        raise SystemExit(2)


@main.command("plan")
@click.option("task_id", "--task-id", default=None, help="id for the new task")
@click.option("--branch", default=None, help="git branch (default: the id, per config)")
@click.option(
    "no_activate", "--no-activate", is_flag=True, help="don't make the new task active"
)
@_apply_agent_options
@_repo_option
def plan_command(
    task_id: str | None,
    branch: str | None,
    no_activate: bool,
    instruction: str,
    instruction_file: str | None,
    agent_cmd: str | None,
    dry_run: bool,
    no_apply: bool,
    context_paths: tuple[str, ...],
    repo_paths: tuple[str, ...],
    action_id: str | None,
    context_id: str | None,
    timeout: int | None,
    unset_env: tuple[str, ...],
    json_output: bool,
    interactive: bool,
) -> None:
    """Draft a task and design its first experiments in one go (`draft` + `design`).

    Give two or three sentences. Whatever the instruction leaves open is looked up in
    the project's default repository and context (`hiraku-agent defaults`), defaulted,
    or chosen and labelled, and listed on each experiment under `## Open items` for you
    to review. Fix any of it afterwards with `update` (or `design --revise`)."""
    _run_agent_command(
        run_plan,
        _resolve_instruction(instruction, instruction_file),
        task_id=task_id,
        branch=branch,
        activate=not no_activate,
        command=resolve_agent_cmd(agent_cmd) if agent_cmd else None,
        dry_run=dry_run,
        apply=not no_apply,
        context_paths=list(context_paths),
        repo_paths=list(repo_paths),
        action=action_id,
        context_id=context_id,
        unset_env=list(unset_env),
        interactive=interactive,
        hooks=None if dry_run or interactive else _hooks(),
        timeout=timeout,
        json_output=json_output,
        no_apply=no_apply,
    )


@main.command("defaults")
@click.option(
    "repos",
    "--repo",
    multiple=True,
    type=click.Path(file_okay=False),
    help="a repository every agent command may read by default; repeatable",
)
@click.option(
    "context_files",
    "--context",
    multiple=True,
    type=click.Path(dir_okay=False),
    help="a file added to every agent command's context; repeatable",
)
@click.option(
    "context_id", "--context-id", default=None, help="a Context record used by default"
)
@click.option(
    "sandbox",
    "--sandbox",
    type=click.Choice(["on", "off"]),
    default=None,
    help="write protection for every agent call (see below)",
)
@click.option(
    "allow_write",
    "--sandbox-allow-write",
    multiple=True,
    help="with the sandbox: a directory agents may also write to; repeatable",
)
@click.option(
    "deny_write",
    "--sandbox-deny-write",
    multiple=True,
    help="with the sandbox: a path agents may never write to; repeatable",
)
@click.option(
    "implement_timeout",
    "--implement-timeout",
    type=click.IntRange(min=1),
    default=None,
    help="seconds an implement call may take (default 3600)",
)
@click.option("clear", "--clear", is_flag=True, help="remove the project defaults")
def defaults_command(
    repos: tuple[str, ...],
    context_files: tuple[str, ...],
    context_id: str | None,
    sandbox: str | None,
    allow_write: tuple[str, ...],
    deny_write: tuple[str, ...],
    implement_timeout: int | None,
    clear: bool,
) -> None:
    """Show or set the project's agent defaults (`hiraku/agent.yaml`).

    With no options, prints them. Each option replaces that setting and leaves the
    others as they are. Repos and files are read-only context, added to whatever a call
    names; an explicit `--context-id` on a call wins over the default one.

    `--sandbox on` protects files from every agent call: the default repos and
    `--sandbox-deny-write` paths cannot be written, and commands an agent runs may
    write only in its working directory, the temp directory and
    `--sandbox-allow-write` directories (Claude Code's sandbox; on Linux it needs
    bubblewrap and socat). `hiraku-agent --no-sandbox <command>` skips it once."""
    config = HirakuConfig.discover()
    try:
        if clear:
            path = defaults_path(config)
            if path.is_file():
                path.unlink()
            click.echo("project defaults removed")
            return
        current = load_defaults(config)
        changed = False
        if repos:
            current.repos = [str(Path(r).expanduser()) for r in repos]
            changed = True
        if context_files:
            current.context_paths = list(context_files)
            changed = True
        if context_id:
            current.context_id = context_id
            changed = True
        if sandbox is not None:
            current.sandbox = sandbox == "on"
            changed = True
        if allow_write:
            current.sandbox_allow_write = [
                str(Path(p).expanduser()) for p in allow_write
            ]
            changed = True
        if deny_write:
            current.sandbox_deny_write = [str(Path(p).expanduser()) for p in deny_write]
            changed = True
        if implement_timeout:
            current.implement_timeout = implement_timeout
            changed = True
        if changed:
            click.echo(f"wrote {save_defaults(current, config)}")
            current = load_defaults(config)
    except HirakuError as exc:
        raise click.ClickException(str(exc)) from exc
    if current.is_empty():
        click.echo(
            "no project defaults. Set them with "
            "`hiraku-agent defaults --repo PATH [--context-id ID] [--context FILE]`."
        )
        return
    for repo in current.repos:
        click.echo(f"repo: {repo}")
    for path_text in current.context_paths:
        click.echo(f"context file: {path_text}")
    if current.context_id:
        click.echo(f"context: {current.context_id}")
    click.echo(
        f"implement timeout: {current.implement_timeout or DEFAULT_IMPLEMENT_TIMEOUT}s"
        + ("" if current.implement_timeout else " (default)")
    )
    click.echo(f"sandbox: {'on' if current.sandbox else 'off'}")
    if current.sandbox:
        protected = [*current.repos, *current.sandbox_deny_write]
        click.echo("  no writes to: " + (", ".join(protected) or "(nothing listed)"))
        if current.sandbox_allow_write:
            click.echo("  may also write: " + ", ".join(current.sandbox_allow_write))
        missing = missing_requirements()
        if missing:
            click.echo(
                "  WARNING: this machine lacks " + ", ".join(missing) + "; agent calls "
                "will be refused until they are installed (or run with --no-sandbox)."
            )


@main.command("update")
@click.argument("record_id")
@click.option(
    "force",
    "--force",
    is_flag=True,
    help="task with experiments that have no runs yet: update the whole task and "
    "send its experiments back to proposed, re-synced with it",
)
@_apply_agent_options
@_repo_option
def update_command(
    record_id: str,
    force: bool,
    instruction: str,
    instruction_file: str | None,
    agent_cmd: str | None,
    dry_run: bool,
    no_apply: bool,
    context_paths: tuple[str, ...],
    repo_paths: tuple[str, ...],
    action_id: str | None,
    context_id: str | None,
    timeout: int | None,
    unset_env: tuple[str, ...],
    json_output: bool,
    interactive: bool,
) -> None:
    """Revise an existing task or experiment from an instruction.

    An experiment can be updated until it has runs. A task can be updated freely until
    experiments refer to it; after that only its Decision rule, unless --force, which
    updates the whole task and sends every experiment back to proposed (commit unbound,
    approval cleared), re-synced with the new task. --force is refused once any
    experiment has runs, a verdict or a launch in flight: draft a new task then."""
    resolved = _resolve_instruction(instruction, instruction_file)
    _run_agent_command(
        run_update,
        record_id,
        force=force,
        instruction=resolved,
        command=resolve_agent_cmd(agent_cmd) if agent_cmd else None,
        dry_run=dry_run,
        apply=not no_apply,
        context_paths=list(context_paths),
        repo_paths=list(repo_paths),
        action=action_id,
        context_id=context_id,
        unset_env=list(unset_env),
        interactive=interactive,
        hooks=None if dry_run or interactive else _hooks(),
        timeout=timeout,
        json_output=json_output,
        no_apply=no_apply,
    )


@main.command("design")
@click.option("task_id", "--task", default=None)
@click.option(
    "revise",
    "--revise",
    is_flag=True,
    help="revise the experiments already designed under the task instead of adding "
    "new ones (the task itself is not changed; use `update` for that)",
)
@_apply_agent_options
@_repo_option
def design_command(
    task_id: str | None,
    revise: bool,
    instruction: str,
    instruction_file: str | None,
    agent_cmd: str | None,
    dry_run: bool,
    no_apply: bool,
    context_paths: tuple[str, ...],
    repo_paths: tuple[str, ...],
    action_id: str | None,
    context_id: str | None,
    timeout: int | None,
    unset_env: tuple[str, ...],
    json_output: bool,
    interactive: bool,
) -> None:
    """Propose a tree of experiments for a task, or with --revise, revise the ones
    already there."""
    config = HirakuConfig.discover()
    resolved_task = _resolve_task(config, task_id)
    _run_agent_command(
        run_revise if revise else run_design,
        resolved_task,
        instruction=_resolve_instruction(instruction, instruction_file),
        command=resolve_agent_cmd(agent_cmd) if agent_cmd else None,
        dry_run=dry_run,
        apply=not no_apply,
        context_paths=list(context_paths),
        repo_paths=list(repo_paths),
        action=action_id,
        context_id=context_id,
        unset_env=list(unset_env),
        interactive=interactive,
        hooks=None if dry_run or interactive else _hooks(),
        timeout=timeout,
        json_output=json_output,
        no_apply=no_apply,
    )


@main.command("discover")
@click.option("task_id", "--task", default=None)
@click.option(
    "on",
    "--on",
    default=None,
    help="the record to investigate: the task (default) or any node beneath it",
)
@click.option(
    "findings_only",
    "--findings-only",
    is_flag=True,
    help="record findings and inferences only; propose no nodes",
)
@_apply_agent_options
@_repo_option
def discover_command(
    task_id: str | None,
    on: str | None,
    findings_only: bool,
    instruction: str,
    instruction_file: str | None,
    agent_cmd: str | None,
    dry_run: bool,
    no_apply: bool,
    context_paths: tuple[str, ...],
    repo_paths: tuple[str, ...],
    action_id: str | None,
    context_id: str | None,
    timeout: int | None,
    unset_env: tuple[str, ...],
    json_output: bool,
    interactive: bool,
) -> None:
    """Search for candidate hypotheses for a task, from the literature or an idea you
    give it — and/or investigate what already exists (files, logs, code) for the task or
    one of its nodes (--on), recording cited findings and unverified inferences in that
    record's own file."""
    config = HirakuConfig.discover()
    try:
        if task_id is None and on is not None:
            task_id = task_of(load_record(on, config=config), config=config).id
        resolved_task = _resolve_task(config, task_id)
    except HirakuError as exc:
        raise click.ClickException(str(exc)) from exc
    _run_agent_command(
        run_discover,
        resolved_task,
        on=on,
        findings_only=findings_only,
        instruction=_resolve_instruction(instruction, instruction_file),
        command=resolve_agent_cmd(agent_cmd) if agent_cmd else None,
        dry_run=dry_run,
        apply=not no_apply,
        context_paths=list(context_paths),
        repo_paths=list(repo_paths),
        action=action_id,
        context_id=context_id,
        unset_env=list(unset_env),
        interactive=interactive,
        hooks=None if dry_run or interactive else _hooks(),
        timeout=timeout,
        json_output=json_output,
        no_apply=no_apply,
    )


@main.command("bind")
@click.argument("node_id")
@click.option("task_id", "--task")
@click.option(
    "commit",
    "--commit",
    default=None,
    metavar="REF",
    help="bind this commit instead of the newest one on the node's branch (it must "
    "be on that branch)",
)
@click.option(
    "keep_runs",
    "--keep-runs",
    is_flag=True,
    help="allow it when the node already has runs; they stay attributed to the "
    "commit they ran",
)
@click.option("yes", "--yes", "-y", is_flag=True, help="don't ask before binding")
@click.option("dry_run", "--dry-run", is_flag=True, help="check and show, bind nothing")
def bind_command(
    node_id: str,
    task_id: str | None,
    commit: str | None,
    keep_runs: bool,
    yes: bool,
    dry_run: bool,
) -> None:
    """Bind the newest commit on NODE_ID's branch to the node, without an agent.

    For code you wrote yourself (or with an interactive session) on the node's branch.
    The same checks as `implement`: the commit must be new, on the node's own branch and
    build on the commit the node is bound to; a node with runs needs --keep-runs; and
    the node's entrypoints must still run against it. Nothing is written until you
    confirm."""
    config = HirakuConfig.discover()

    def show(plan: BindPlan) -> None:
        click.echo(f"{plan.node_id}: bind {plan.commit[:12]} (branch {plan.branch})")
        if plan.previous:
            click.echo(f"  now bound to {plan.previous[:12]}")
        for line in plan.commits:
            click.echo(f"  + {line}")
        if plan.earlier_runs:
            click.echo(
                f"  {len(plan.earlier_runs)} earlier run(s) stay attributed to "
                f"{(plan.previous or '')[:12]}"
            )
        for caution in plan.cautions:
            click.echo(f"  check: {caution}")
        if plan.uncommitted:
            click.echo(f"  not part of it: uncommitted changes in {plan.worktree}:")
            for line in plan.uncommitted[:10]:
                click.echo(f"    {line}")

    def confirm(plan: BindPlan) -> bool:
        show(plan)
        return yes or click.confirm("Bind it?", default=False)

    try:
        plan = run_bind(
            node_id,
            task_id=task_id,
            commit=commit,
            keep_runs=keep_runs,
            dry_run=dry_run,
            confirm=None if dry_run else confirm,
            config=config,
        )
    except HirakuError as exc:
        raise click.ClickException(str(exc)) from exc
    if dry_run:
        show(plan)
        click.echo("(dry run: nothing was bound)")
    else:
        click.echo(f"Bound {plan.node_id} to {plan.commit}")


@main.command("implement")
@click.argument("node_ids", nargs=-1)
@click.option("task_id", "--task", default=None)
@click.option(
    "implement_all",
    "--all",
    is_flag=True,
    help="every approved node of the task that has no code yet, parents first",
)
@click.option("yes", "--yes", "-y", is_flag=True, help="with --all: don't ask first")
@click.option(
    "resume",
    "--resume",
    is_flag=True,
    help="continue an earlier stopped or failed attempt in its kept worktree, "
    "instead of starting over (with several nodes: those that have one)",
)
@click.option(
    "from_log",
    "--from-log",
    default=None,
    metavar="LOG_DIR",
    help="don't call the agent: bind using the reply an earlier implement call on "
    "this node logged in LOG_DIR (hiraku/agent-logs/...), e.g. after hiraku-agent "
    "crashed once the agent had finished",
)
@click.option(
    "revise",
    "--revise",
    is_flag=True,
    help="change the code of nodes that already have it, as -m says, and rebind "
    "them to the new commit (only while they have no runs, unless --keep-runs; with "
    "--all: every such node of the task)",
)
@click.option(
    "fast",
    "--fast",
    is_flag=True,
    help="quick mode: the agent runs no cluster job and no experiment code, only "
    "static checks (syntax, imports, entrypoint arguments, run recording on "
    "synthetic numbers), and says in its notes what stays unverified",
)
@click.option(
    "keep_runs",
    "--keep-runs",
    is_flag=True,
    help="with --revise: revise nodes that already have runs too; those runs stay "
    "attributed to the commit they ran (each run records it) and are listed in the "
    "revision notes. Still refused while a launch of the node is in flight",
)
@click.option(
    "jobs",
    "--jobs",
    "-j",
    type=click.IntRange(min=1),
    default=1,
    show_default=True,
    help="with several nodes: how many to implement at the same time (a child still "
    "waits for its parent)",
)
@_apply_agent_options
def implement_command(
    node_ids: tuple[str, ...],
    task_id: str | None,
    implement_all: bool,
    yes: bool,
    resume: bool,
    from_log: str | None,
    revise: bool,
    keep_runs: bool,
    fast: bool,
    jobs: int,
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
    interactive: bool,
) -> None:
    """Write approved proposals as real code, and bind the resulting commits.

    Name one or more nodes, or pass --all for every approved node of the task that has
    no code yet. Several nodes run one after another, parents before children: a node
    under another node starts from its parent's code, so a child is skipped when its
    parent could not be implemented. A failure does not stop the others; the summary
    says what happened to each. The instruction (-m / --instruction-file) goes to every
    node.

    Each node must already be approved (`hiraku-agent approve`) — in guided mode that
    approval is the human trigger; the agent is what writes the code either way. Unlike
    every other workflow here, the agent is given file-write and shell tools (see
    `workflows.run_implement`'s docstring), so review what it wrote with `git diff`/`git
    log` on the commit it reports before trusting it.

    An attempt that is stopped (timeout) or fails keeps its uncommitted changes in its
    worktree and its commits on the node branch. `--resume` continues it there: the
    agent is told what the earlier attempt committed, left uncommitted and reported,
    and the earlier commits count towards the one that gets bound. Without `--resume`,
    a node whose earlier worktree is still kept is refused rather than started over.
    """
    if implement_all == bool(node_ids):
        raise click.UsageError("Name the nodes to implement, or pass --all (not both).")
    if from_log and len(node_ids) != 1:
        raise click.UsageError("--from-log takes exactly one node.")
    if keep_runs and not revise:
        raise click.UsageError("--keep-runs only goes with --revise.")
    if interactive and (implement_all or len(node_ids) != 1 or jobs > 1):
        raise click.UsageError(
            "--interactive implements one node: name exactly one, without --all or "
            "--jobs."
        )
    if revise and not (instruction or instruction_file or from_log):
        raise click.UsageError(
            "--revise needs -m (or --instruction-file): what to change."
        )
    kwargs: dict[str, Any] = {
        "instruction": _resolve_instruction(instruction, instruction_file),
        "command": resolve_agent_cmd(agent_cmd) if agent_cmd else None,
        "apply": not no_apply,
        "context_paths": list(context_paths),
        "action": action_id,
        "context_id": context_id,
        "unset_env": list(unset_env),
        "hooks": None if dry_run or interactive else _hooks(),
        "timeout": timeout,
        "interactive": interactive,
    }
    if len(node_ids) == 1:
        _run_agent_command(
            run_implement,
            node_ids[0],
            task_id=task_id,
            dry_run=dry_run,
            json_output=json_output,
            no_apply=no_apply,
            resume=resume,
            from_log=from_log,
            revise=revise,
            keep_runs=keep_runs,
            fast=fast,
            **kwargs,
        )
        return

    config = HirakuConfig.discover()
    try:
        resolved_task = _resolve_task(config, task_id)
        kwargs, notices = merge_defaults(kwargs, config)
        if implement_all and revise:
            nodes = list_nodes(task=resolved_task, config=config)
            runs_by_id, states, _ = node_bookkeeping(nodes, config=config)
            targets = [
                node
                for node in nodes
                if node.commit
                and not node.abandoned
                and (keep_runs or not runs_by_id.get(node.id))
                and states.get(node.id) != "running"
            ]
        elif implement_all:
            targets = [
                node
                for node in list_nodes(task=resolved_task, config=config)
                if node.is_proposal and not node.abandoned and is_approved(node)
            ]
        else:
            targets = [
                load_node(node_id, task=resolved_task, config=config)
                for node_id in node_ids
            ]
    except HirakuError as exc:
        raise click.ClickException(str(exc)) from exc
    for notice in notices:
        click.echo(f"defaults: {notice}", err=True)
    if not targets:
        if revise:
            click.echo(
                f"Nothing in {resolved_task} can be revised: nodes need code"
                + ("" if keep_runs else " and no runs")
                + ", and no launch in flight."
            )
        else:
            click.echo(f"Nothing in {resolved_task} is approved and waiting for code.")
        return
    ordered = _parents_first(targets)
    jobs = 1 if dry_run else min(jobs, len(ordered))
    verb = "revise" if revise else "implement"
    if implement_all and not (yes or dry_run):
        how = f"up to {jobs} at a time" if jobs > 1 else "in order"
        click.echo(f"{len(ordered)} node(s) in {resolved_task} to {verb}, {how}:")
        for node in ordered:
            click.echo(f"  {node.id}: {node.expectation or '(no expectation)'}")
        click.confirm(
            f"{verb.capitalize()} all {len(ordered)}? Each is a separate agent call.",
            abort=True,
        )

    def implement_one(node: Any, hooks: Hooks | None) -> tuple[str, bool, Any]:
        """Implement one node; return (outcome, implemented, result or None)."""
        try:
            task_record = task_of(node, config=config)
            resuming = (
                resume
                and not revise
                and resume_state(config, node, task_record) is not None
            )
            if resuming:
                click.echo(f"{node.id}: resuming the earlier attempt", err=True)
            result = run_implement(
                node.id,
                task_id=resolved_task,
                config=config,
                dry_run=dry_run,
                resume=resuming,
                revise=revise,
                keep_runs=keep_runs,
                fast=fast,
                **{**kwargs, "hooks": hooks},
            )
        except HirakuError as exc:
            click.echo(f"{node.id}: {exc}", err=True)
            return f"failed: {str(exc).splitlines()[0]}", False, None
        except Exception as exc:  # one node's bug must not end the whole batch
            click.echo(f"{node.id}: {type(exc).__name__}: {exc}", err=True)
            return f"failed: {type(exc).__name__}: {exc}", False, None
        if dry_run:
            return "dry run", True, result
        live = load_node(node.id, task=resolved_task, config=config)
        if revise and live.commit and live.commit != node.commit:
            return (
                f"revised: {str(node.commit)[:12]} -> {live.commit[:12]}",
                True,
                result,
            )
        if revise:
            reason = result.data.get("blocked") or "no new commit"
            return f"not revised: {str(reason).splitlines()[0]}", False, result
        if live.commit:
            return f"implemented at {live.commit[:12]}", True, result
        reason = result.data.get("blocked") or (
            "--no-apply" if no_apply else "no commit bound"
        )
        return f"not implemented: {str(reason).splitlines()[0]}", False, result

    if jobs > 1:

        def report(node: Any, result: Any) -> None:
            click.echo(f"== {node.id}")
            _print_result(result, json_output=json_output, no_apply=no_apply)

        outcome, not_done = _implement_in_parallel(
            ordered, jobs, implement_one, report, independent=revise
        )
        _summary(ordered, outcome)
        if not no_apply and not_done:
            raise SystemExit(1)
        return

    outcome: dict[str, str] = {}
    not_done: set[str] = set()
    for index, node in enumerate(ordered, 1):
        click.echo(f"== [{index}/{len(ordered)}] {node.id}")
        # A revision of a parent does not reach its children's branches, so they do
        # not depend on it.
        if node.parent_id in not_done and not revise:
            outcome[node.id] = (
                f"skipped: its parent {node.parent_id} was not implemented"
            )
            not_done.add(node.id)
            click.echo(outcome[node.id])
            continue
        outcome[node.id], ok, result = implement_one(node, kwargs["hooks"])
        if not ok:
            not_done.add(node.id)
        if result is not None:
            if dry_run:
                click.echo(result.prompt)
            else:
                _print_result(result, json_output=json_output, no_apply=no_apply)

    _summary(ordered, outcome)
    if not dry_run and not no_apply and not_done:
        raise SystemExit(1)


def _summary(ordered: list[Any], outcome: dict[str, str]) -> None:
    click.echo("summary:")
    for node in ordered:
        click.echo(f"  {node.id}: {outcome.get(node.id, '?')}")


def _implement_in_parallel(
    ordered: list[Any],
    jobs: int,
    implement_one: Callable[[Any, Hooks | None], tuple[str, bool, Any]],
    report: Callable[[Any, Any], None],
    *,
    independent: bool = False,
) -> tuple[dict[str, str], set[str]]:
    """Run `implement_one` on up to `jobs` nodes at once, parents before children.

    A node whose parent is in the batch starts once the parent is done, and is skipped
    if the parent was not implemented. Each call's output lines start with its node id.
    Ctrl-C stops every running agent; their work is kept as in a single call.
    """
    # `independent` (revisions): a child does not wait for, or depend on, its parent.
    in_batch = set() if independent else {node.id for node in ordered}
    pending = list(ordered)
    outcome: dict[str, str] = {}
    finished: set[str] = set()
    not_done: set[str] = set()
    click.echo(f"implementing {len(ordered)} node(s), up to {jobs} at a time", err=True)
    pool = ThreadPoolExecutor(max_workers=jobs, thread_name_prefix="implement")
    running: dict[Future[tuple[str, bool, Any]], Any] = {}
    try:
        while pending or running:
            for node in list(pending):
                if len(running) >= jobs:
                    break
                parent = node.parent_id
                if parent in not_done and not independent:
                    pending.remove(node)
                    outcome[node.id] = (
                        f"skipped: its parent {parent} was not implemented"
                    )
                    not_done.add(node.id)
                    click.echo(f"[{node.id}] {outcome[node.id]}", err=True)
                    continue
                if parent in in_batch and parent not in finished:
                    continue  # waits for its parent
                pending.remove(node)
                running[pool.submit(implement_one, node, _hooks(prefix=node.id))] = node
            if not running:
                if pending:  # nothing can start: should not happen (parents first)
                    for node in pending:
                        outcome[node.id] = "skipped: its parent never ran"
                        not_done.add(node.id)
                    pending.clear()
                continue
            done, _ = wait(list(running), return_when=FIRST_COMPLETED)
            for future in done:
                node = running.pop(future)
                text, ok, result = future.result()
                outcome[node.id] = text
                finished.add(node.id)
                if not ok:
                    not_done.add(node.id)
                if result is not None:
                    report(node, result)
                click.echo(f"[{node.id}] {text}", err=True)
    except KeyboardInterrupt:
        stopped = stop_all_agents()
        click.echo(
            f"interrupted: stopped {stopped} running agent(s); their work is kept "
            f"(continue with `implement <node> --resume`)",
            err=True,
        )
        pool.shutdown(wait=True, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    return outcome, not_done


def _parents_first(nodes: list[Any]) -> list[Any]:
    """`nodes` with every parent before its children, otherwise in the given order."""
    by_id = {node.id: node for node in nodes}
    ordered: list[Any] = []
    placed: set[str] = set()

    def place(node: Any, seen: frozenset[str] = frozenset()) -> None:
        if node.id in placed or node.id in seen:
            return
        parent = by_id.get(node.parent_id)
        if parent is not None:
            place(parent, seen | {node.id})
        placed.add(node.id)
        ordered.append(node)

    for node in nodes:
        place(node)
    return ordered


@main.command("triage")
@click.argument("node_id")
@click.option("task_id", "--task", default=None)
@_apply_agent_options
@_repo_option
def triage_command(
    node_id: str,
    task_id: str | None,
    instruction: str,
    instruction_file: str | None,
    agent_cmd: str | None,
    dry_run: bool,
    no_apply: bool,
    context_paths: tuple[str, ...],
    repo_paths: tuple[str, ...],
    action_id: str | None,
    context_id: str | None,
    timeout: int | None,
    unset_env: tuple[str, ...],
    json_output: bool,
    interactive: bool,
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
        repo_paths=list(repo_paths),
        action=action_id,
        context_id=context_id,
        unset_env=list(unset_env),
        interactive=interactive,
        hooks=None if dry_run or interactive else _hooks(),
        timeout=timeout,
        json_output=json_output,
        no_apply=no_apply,
    )


@main.command("analyze")
@click.argument("node_id")
@click.option("task_id", "--task", default=None)
@click.option(
    "only_nodes",
    "--nodes",
    multiple=True,
    help="task level only: analyse just these nodes (comma-separated, repeatable)",
)
@click.option(
    "allow_running",
    "--allow-running",
    is_flag=True,
    help="analyse a node whose launch is still in flight (findings marked partial)",
)
@_apply_agent_options
@_repo_option
def analyze_command(
    node_id: str,
    task_id: str | None,
    only_nodes: tuple[str, ...],
    allow_running: bool,
    instruction: str,
    instruction_file: str | None,
    agent_cmd: str | None,
    dry_run: bool,
    no_apply: bool,
    context_paths: tuple[str, ...],
    repo_paths: tuple[str, ...],
    action_id: str | None,
    context_id: str | None,
    timeout: int | None,
    unset_env: tuple[str, ...],
    json_output: bool,
    interactive: bool,
) -> None:
    """Judge NODE_ID's runs against its expectation and write its findings.

    Given a *task* id instead, compare every node under the task and write the answer
    to its question into the task record."""
    _run_agent_command(
        run_analyze,
        node_id,
        task_id=task_id,
        only=[part for item in only_nodes for part in item.split(",") if part.strip()],
        allow_running=allow_running,
        instruction=_resolve_instruction(instruction, instruction_file),
        command=resolve_agent_cmd(agent_cmd) if agent_cmd else None,
        dry_run=dry_run,
        apply=not no_apply,
        context_paths=list(context_paths),
        repo_paths=list(repo_paths),
        action=action_id,
        context_id=context_id,
        unset_env=list(unset_env),
        interactive=interactive,
        hooks=None if dry_run or interactive else _hooks(),
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
@click.option(
    "repo_paths",
    "--repo",
    multiple=True,
    type=click.Path(exists=True, file_okay=False),
    help="a repository every step may read (read-only); repeatable",
)
@click.option(
    "context_paths",
    "--context",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False),
    help="a file added to every step's context; repeatable",
)
@click.option("context_id", "--context-id", default=None, help="a Context record")
def loop_command(
    program_id: str,
    task_id: str,
    max_steps: int | None,
    json_output: bool,
    repo_paths: tuple[str, ...],
    context_paths: tuple[str, ...],
    context_id: str | None,
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
            repo_paths=list(repo_paths),
            context_paths=list(context_paths),
            context_id=context_id,
            config=config,
        )
    except LoopFailed as exc:
        # Say what already happened before the failing step: those writes stand.
        for step in exc.result.steps:
            click.echo(
                f"done before the failure: {step.action} "
                f"{step.node or task_id} ({len(step.written)} file(s) written)",
                err=True,
            )
        raise click.ClickException(str(exc)) from exc
    except HirakuError as exc:
        raise click.ClickException(str(exc)) from exc
    for notice in result.notices:
        click.echo(f"defaults: {notice}", err=True)
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
