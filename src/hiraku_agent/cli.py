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
import shlex
from pathlib import Path
from typing import Any

import click

from hiraku.config import HirakuConfig
from hiraku.errors import HirakuError, NoActiveRecordError
from hiraku.record import list_records, load_record

from .cleanup import delete_subtree, describe, plan_delete, refusal
from .defaults import (
    AgentDefaults,
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
from .engine import AgentRun, Hooks
from .engine import agent_command as resolve_agent_cmd
from .errors import AgentError
from .gates import approve as approve_node
from .gates import is_approved
from .launches import mark_done, mark_running, running_nodes
from .loop import LoopFailed, LoopResult, run_loop
from .node import list_nodes, load_node, task_of
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


def _resolve_task(config: HirakuConfig, task_id: str | None) -> str:
    if task_id is not None:
        return task_id
    if config.active_record_id is not None:
        return config.active_record_id
    raise NoActiveRecordError(
        "No task given, and no active record is set (`hiraku record set <id>`)"
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
        hooks=None if dry_run else _hooks(),
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
        hooks=None if dry_run else _hooks(),
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
        hooks=None if dry_run else _hooks(),
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
@click.option("clear", "--clear", is_flag=True, help="remove the project defaults")
def defaults_command(
    repos: tuple[str, ...],
    context_files: tuple[str, ...],
    context_id: str | None,
    clear: bool,
) -> None:
    """Show or set what every agent command reads by default (`hiraku/agent.yaml`).

    With no options, prints the current defaults. With options, replaces them. Repos
    and files are read-only context, added to whatever a call names; an explicit
    `--context-id` on a call wins over the default one."""
    config = HirakuConfig.discover()
    try:
        if clear:
            path = defaults_path(config)
            if path.is_file():
                path.unlink()
            click.echo("project defaults removed")
            return
        if repos or context_files or context_id:
            path = save_defaults(
                AgentDefaults(
                    repos=[str(Path(r).expanduser()) for r in repos],
                    context_paths=list(context_files),
                    context_id=context_id,
                ),
                config,
            )
            click.echo(f"wrote {path}")
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
        hooks=None if dry_run else _hooks(),
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
        hooks=None if dry_run else _hooks(),
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
        hooks=None if dry_run else _hooks(),
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
