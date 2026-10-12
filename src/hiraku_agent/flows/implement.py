"""`implement`: the one workflow where the agent writes code."""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from hiraku.config import HirakuConfig
from hiraku.errors import HirakuError

from .. import sections
from ..defaults import load_defaults
from ..engine import (
    DEFAULT_IMPLEMENT_AGENT_CMD,
    DEFAULT_IMPLEMENT_TIMEOUT,
    IMPLEMENT_AGENT_CMD_ENV_VAR,
    TRANSCRIPT_NAME,
    AgentRun,
    Hooks,
    agent_command,
    extract_json,
    read_logs,
    reply_from_transcript,
    transcript_steps,
)
from ..entrypoints import entrypoint_problems
from ..errors import AgentError, AgentTimeout
from ..gates import is_approved
from ..lock import writes
from ..node import (
    list_nodes,
    load_node,
    task_of,
)
from ..replies import ImplementReply
from ..state import tree_facts
from ..worktree import (
    PROGRESS_FILE,
    Checkout,
    ProgressWatcher,
    open_checkout,
    repository_root,
    resume_state,
    revise_state,
)
from ._common import AgentCall, task_state

# -------------------------------------------------------------------------- implement


@writes("implement", per_target=True)
def run_implement(
    node_id: str,
    *,
    task_id: str | None = None,
    instruction: str = "",
    command: list[str] | None = None,
    dry_run: bool = False,
    apply: bool = True,
    context_paths: list[str] | None = None,
    context_bytes: int | None = None,
    unset_env: list[str] | None = None,
    hooks: Hooks | None = None,
    timeout: int | None = None,
    action: str | None = None,
    context_id: str | None = None,
    resume: bool = False,
    from_log: str | Path | None = None,
    revise: bool = False,
    keep_runs: bool = False,
    fast: bool = False,
    interactive: bool = False,
    config: HirakuConfig | None = None,
) -> AgentRun:
    """Write the real code for an approved proposal, then bind the resulting commit.

    The one workflow in this module where the agent writes files — see `engine.py`'s
    `DEFAULT_IMPLEMENT_AGENT_CMD` and `prompts/implement.md`'s "Exception to the rule
    above" for why that is scoped narrowly and separately from everything else here.

    The approval gate is checked here too, not only in `schedule.suggest_next` (which
    only *names* `implement` once `is_approved` is already true): this function is also
    reachable directly from `hiraku-agent implement <id>`, bypassing the scheduler
    entirely, so the gate has to hold at the point where code actually gets written, not
    only at the point where the scheduler makes a suggestion. In `guided` mode this is
    the human trigger the rest of this workflow's write access depends on; in `auto`
    mode `is_approved` is already unconditionally true (`gates.is_approved`).

    With `resume`, the call continues an earlier attempt instead of starting over: it
    runs in the worktree that attempt left behind (or a fresh one on the same branch,
    if it left only commits), the prompt's state says what is already there, and
    commits the earlier attempt made on the node branch count towards the binding.

    With `from_log`, no agent is called: the reply logged in that `agent-logs`
    directory by an earlier `implement` call on this node is applied again, as
    `--resume` would bind it. For a call whose reply was fine but whose binding did
    not happen (hiraku-agent crashed or was stopped after the agent finished).

    With `revise`, the node already has code: the agent changes it as `instruction`
    says, on the same branch, and the node is rebound to the new commit, which must
    build on the old one. Only while the node has no runs and nothing in flight, since
    runs are facts about the commit they ran; `keep_runs` allows it when the node has
    runs (never with one in flight): each run row records the commit it ran, so the
    earlier runs stay attributed to the old commit, and the revision notes list them.
    A revision stopped half way is picked up by the next `--revise` (its worktree and
    commits are reused).

    With `fast`, the agent runs no cluster job and no experiment code: it checks the
    change statically (syntax, imports, the rendered entrypoints' arguments, run
    recording against synthetic numbers) and says in `notes` what it left unverified.
    """
    selected = config or HirakuConfig.discover()
    node = load_node(node_id, task=task_id, config=selected)
    if node.abandoned:
        raise HirakuError(f"'{node.id}' is abandoned — nothing to implement.")
    earlier_runs: list[dict[str, Any]] = []
    if keep_runs and not revise:
        raise AgentError("`--keep-runs` only goes with `--revise`.")
    if revise:
        earlier_runs = _check_revisable(
            node, instruction, config=selected, keep_runs=keep_runs
        )
    elif not node.is_proposal:
        raise HirakuError(
            f"'{node.id}' already has a commit bound ({node.commit}) — nothing to "
            f"implement. To change its code, run `hiraku-agent implement {node.id} "
            f"--revise -m '<what to change>'` (while it has no runs)."
        )
    if not is_approved(node):
        raise AgentError(
            f"'{node.id}' is not approved yet. Run `hiraku-agent approve {node.id} "
            f"--task {task_of(node, config=selected).id} --by <you>` first — in guided "
            f"mode that approval is what authorizes this workflow to write real code."
        )

    task = task_of(node, config=selected)
    revising_from = node.commit if revise else None
    if from_log is not None:
        return _apply_logged_reply(
            selected,
            node,
            task,
            Path(from_log),
            apply=apply,
            revising_from=revising_from,
            earlier_runs=earlier_runs,
        )
    nodes = list_nodes(task=task, config=selected)
    # No `repo_paths`: `--repo` grants read-only tools, and this workflow's command
    # needs write access, so the two are not combined.
    call = AgentCall.resolve(
        selected,
        interactive=interactive,
        workflow="implement",
        entity=task.id,
        action=action,
        context_id=context_id,
        command=command,
        timeout=timeout,
        context_paths=context_paths,
        context_bytes=context_bytes,
        hooks=hooks,
        unset_env=unset_env,
    )
    call.timeout = (
        call.timeout
        or load_defaults(selected).implement_timeout
        or DEFAULT_IMPLEMENT_TIMEOUT
    )
    state = task_state(task, nodes, config=selected, focus=node.id)
    state["time_budget_minutes"] = max(1, round(call.timeout / 60))
    state["tracking_backend"] = selected.tracking_backend
    if fast:
        state["fast"] = True
    if revise:
        state["revise"] = revise_state(selected, node)
        if earlier_runs:
            state["revise"]["runs_on_bound_commit"] = earlier_runs
    elif resume:
        if repository_root(selected) is None:
            raise AgentError("`--resume` needs the project to be in a git repository.")
        left = resume_state(selected, node, task)
        if left is None:
            raise AgentError(
                f"Nothing to resume for '{node.id}': no earlier attempt left commits "
                f"on its branch or changes in a kept worktree. Run "
                f"`hiraku-agent implement {node.id}` without `--resume`."
            )
        previous = previous_attempt(selected, node.id)
        if previous:
            left["previous_attempt"] = previous
        state["resume"] = left
    result = call.prepare("implement", state, instruction=instruction)
    if dry_run:
        return result

    # Unlike every other workflow, an unresolved `command` here must NOT fall back to
    # `agent_command()`'s plain `claude -p`: that default denies file edits
    # non-interactively (see `engine.KNOWN_FAILURES`), so this call would run but could
    # never actually implement anything. Resolve the implement-specific, separately
    # named default instead — still overridable the same way everything else is
    # (`--agent-cmd`, a bound `Action`'s `agent_cmd`, or this one's own env var).
    call.command = call.command or agent_command(
        env_var=IMPLEMENT_AGENT_CMD_ENV_VAR, default=DEFAULT_IMPLEMENT_AGENT_CMD
    )

    if repository_root(selected) is None:
        raise AgentError(
            "`implement` needs the project to be in a git repository: the agent works "
            "in a separate worktree on the node's own branch, and the node is bound to "
            "the commit it makes there."
        )
    checkout = open_checkout(
        selected, node, task, resume=resume, start_at=revising_from
    )
    watcher = _watch_progress(call, checkout)
    try:
        call.ask(result, node.id, reply=ImplementReply, cwd=checkout.cwd)
    except Exception as exc:
        if watcher:
            watcher.poll()
        # Stopped mid-edit, or failed: nothing is bound and nothing is discarded.
        committed = checkout.moved()
        progress = checkout.progress()
        kept = checkout.finish(keep_progress=True)
        left = checkout.status() if kept else ""
        if left:
            detail = (
                f"The agent's uncommitted changes are kept in {checkout.root}:\n{left}"
            )
        elif kept:
            detail = f"Its worktree is kept in {checkout.root}."
        else:
            detail = "It left no uncommitted changes."
        if revising_from is not None:
            unbound = f"'{node.id}' was not rebound; it stays at {revising_from[:12]}."
        else:
            unbound = f"'{node.id}' was not bound to any commit."
        message = f"{exc}\n{unbound} {detail}"
        if progress:
            message += (
                f"\nIts progress notes ({PROGRESS_FILE}), last part:\n"
                + (progress[-1500:])
            )
        if kept or committed:
            again = "--revise -m '...'" if revising_from else "--resume"
            message += (
                f"\nContinue from there with `hiraku-agent implement {node.id} "
                f"{again}`."
            )
        if isinstance(exc, AgentTimeout):
            raise AgentTimeout(message) from exc
        if isinstance(exc, AgentError):
            raise AgentError(message) from exc
        raise
    if watcher:
        watcher.poll()
    return _bind(
        selected,
        node,
        task,
        checkout,
        result,
        apply=apply,
        revising_from=revising_from,
        earlier_runs=earlier_runs,
    )


def _check_revisable(
    node: Any, instruction: str, *, config: HirakuConfig, keep_runs: bool = False
) -> list[dict[str, Any]]:
    """Refuse a revision that is not allowed; return the node's runs (id and commit)
    that a permitted one leaves attributed to the old commit."""
    if node.is_proposal:
        raise AgentError(
            f"'{node.id}' has no code yet — nothing to revise. Run "
            f"`hiraku-agent implement {node.id}`."
        )
    if not instruction.strip():
        raise AgentError(
            "`--revise` needs an instruction saying what to change (-m '...')."
        )
    return revision_runs(node, config=config, keep_runs=keep_runs)


def revision_runs(
    node: Any, *, config: HirakuConfig, keep_runs: bool = False
) -> list[dict[str, Any]]:
    """The node's runs (id and commit) a new commit would leave attributed to the old
    one. Refuses while a launch of the node is in flight, and, without `keep_runs`,
    when it has runs at all."""
    nodes = list_nodes(task=task_of(node, config=config), config=config)
    runs_by_id, states, _, _ = tree_facts(nodes, config=config)
    if states.get(node.id) == "running":
        raise AgentError(
            f"'{node.id}' has a launch in flight on commit {str(node.commit)[:12]}; "
            f"wait for it to finish (or `hiraku-agent mark-done {node.id}`) before "
            f"changing its code."
        )
    runs = runs_by_id.get(node.id) or []
    if runs and not keep_runs:
        raise AgentError(
            f"'{node.id}' already has {len(runs)} run(s) on commit "
            f"{str(node.commit)[:12]}; those results belong to that code, so it is not "
            f"revised in place. To change it anyway, with those runs staying "
            f"attributed to {str(node.commit)[:12]}, add `--keep-runs`; or draft a new "
            f"node for the changed code."
        )
    return [
        {"id": getattr(run, "id", None), "commit": getattr(run, "commit", None)}
        for run in runs
    ]


def _watch_progress(call: AgentCall, checkout: Checkout) -> ProgressWatcher | None:
    """Report the agent's progress-file lines live, checked on every tick."""
    hooks = call.hooks
    if hooks is None or hooks.note is None:
        return None
    watcher = ProgressWatcher(checkout.root / PROGRESS_FILE, hooks.note)
    tick = hooks.tick

    def ticked(elapsed: float) -> None:
        watcher.poll()
        if tick:
            tick(elapsed)

    call.hooks = replace(hooks, tick=ticked)
    return watcher


def _bind(
    selected: HirakuConfig,
    node: Any,
    task: Any,
    checkout: Checkout,
    result: AgentRun,
    *,
    apply: bool,
    revising_from: str | None = None,
    earlier_runs: list[dict[str, Any]] | None = None,
) -> AgentRun:
    """Check the reply's commit and bind it to the node (or keep the worktree).

    `revising_from`: the node's commit before a `--revise`; the node must still be
    bound to it, and the notes are added as a revision instead of replacing."""
    data = result.data
    if data.get("blocked") or not apply:
        kept = checkout.finish()
        if kept:
            data["worktree_kept"] = str(checkout.root)
        return result

    try:
        resolved_commit = _bound_commit(checkout, str(data["commit"]))
    except AgentError as exc:
        raise _refuse(checkout, node, str(exc), revising_from) from exc

    # Reload: an agent call that just edited and committed files is exactly the case
    # `run_triage`/`run_analyze`'s own "an agent call is minutes long" reload guards
    # against, and here the stakes are higher — binding a commit is the one write in
    # this module that is not easily reverted by editing a markdown file back.
    live = load_node(node.id, task=task, config=selected)
    if revising_from is not None:
        if live.commit != revising_from:
            raise HirakuError(
                f"'{live.id}' was rebound to {live.commit} while this revision ran "
                f"(it started from {revising_from}). Not overwriting; the revision is "
                f"{resolved_commit} on {checkout.branch}."
            )
    elif not live.is_proposal:
        raise HirakuError(
            f"'{live.id}' already has a commit bound ({live.commit}) — something else "
            f"implemented it while this call was running. Not overwriting; inspect "
            f"both commits by hand."
        )

    entrypoints = data.get("entrypoints")
    merged = dict(live.entrypoints or {})
    if isinstance(entrypoints, dict) and entrypoints:
        merged.update(entrypoints)
    problems, cautions = entrypoint_problems(
        merged, read_file=lambda path: checkout.file_at(resolved_commit, path)
    )
    if problems:
        raise _refuse(
            checkout,
            node,
            "The entrypoints would not run as declared, so the commit was not "
            "bound: " + "; ".join(problems) + ". Fix the script and the entrypoint "
            "together, and test the entrypoint as `hiraku launch` would run it.",
            revising_from,
        )
    if merged:
        live.entrypoints = merged
    if cautions:
        data["notes"] = (
            f"{data.get('notes') or ''}\n\nCheck before launching: "
            + " ".join(cautions)
        ).strip()

    if data.get("notes") and revising_from is not None:
        stamp = _dt.date.today().isoformat()
        entry = (
            f"**Revision {stamp}** ({revising_from[:12]} -> {resolved_commit[:12]}): "
            f"{data['notes']}"
        )
        if earlier_runs:
            ids = ", ".join(str(run["id"]) for run in earlier_runs[:10])
            more = (
                f" (and {len(earlier_runs) - 10} more)"
                if len(earlier_runs) > 10
                else ""
            )
            entry += (
                f"\n\n{len(earlier_runs)} earlier run(s) stay attributed to "
                f"{revising_from[:12]} and were not produced by this revision: "
                f"{ids}{more}."
            )
            data["earlier_runs"] = [run["id"] for run in earlier_runs]
        live.markdown = sections.append_section(
            live.markdown, "Implementation notes", entry
        )
    elif data.get("notes"):
        live.markdown = sections.set_section(
            live.markdown, "Implementation notes", str(data["notes"])
        )

    live.commit = resolved_commit
    if not live.branch:
        live.branch = checkout.branch
    live.save()
    result.written.append(live.path)
    if revising_from is not None:
        data["revised_from"] = revising_from
        children = [
            other.id
            for other in list_nodes(task=task, config=selected)
            if other.parent_id == live.id and other.commit
        ]
        if children:
            data["notes"] = (
                f"{data.get('notes') or ''}\n\nNot in {', '.join(children)}: their "
                f"branches started from the earlier commit. Revise them too if they "
                f"need this change."
            ).strip()
    if checkout.finish():
        data["worktree_kept"] = str(checkout.root)
        data["notes"] = (
            f"{data.get('notes') or ''}\n\nThe agent left uncommitted changes, kept in "
            f"{checkout.root}; they are not part of the bound commit."
        ).strip()
    return result


def _refuse(
    checkout: Checkout, node: Any, reason: str, revising_from: str | None
) -> AgentError:
    """The error for a reply that cannot be bound: keep the worktree and its
    progress file, and say how to continue from them."""
    committed = checkout.moved()
    kept = checkout.finish(keep_progress=True)
    where = f" The worktree is kept at {checkout.root}." if kept else ""
    if kept or committed:
        again = "--revise -m '...'" if revising_from else "--resume -m '...'"
        where += f" Continue with `hiraku-agent implement {node.id} {again}`."
    return AgentError(f"{reason} '{node.id}' was not bound.{where}")


def _apply_logged_reply(
    selected: HirakuConfig,
    node: Any,
    task: Any,
    directory: Path,
    *,
    apply: bool,
    revising_from: str | None = None,
    earlier_runs: list[dict[str, Any]] | None = None,
) -> AgentRun:
    if not directory.is_absolute():
        directory = selected.root / directory
    meta_path, reply_path = directory / "meta.json", directory / "reply.txt"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        raw = reply_path.read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError) as exc:
        raise AgentError(
            f"{directory} is not a finished agent call's log (needs meta.json and "
            f"reply.txt): {exc}"
        ) from exc
    if meta.get("workflow") != "implement" or meta.get("target") != node.id:
        raise AgentError(
            f"{directory} logs `{meta.get('workflow')}` on {meta.get('target')!r}, "
            f"not `implement` on '{node.id}'."
        )
    if repository_root(selected) is None:
        raise AgentError("`implement` needs the project to be in a git repository.")
    result = AgentRun(workflow="implement", prompt="", raw=raw, log=directory)
    try:
        result.data = extract_json(raw)
    except AgentError:
        # The agent may have replied earlier and said something else afterwards.
        recovered = reply_from_transcript(
            directory / TRANSCRIPT_NAME,
            lambda data: ImplementReply.check(data, workflow="implement"),
            ImplementReply.model_fields,
        )
        if recovered is None:
            raise
        result.data = recovered
    ImplementReply.check(result.data, workflow="implement")
    checkout = open_checkout(selected, node, task, resume=True, start_at=revising_from)
    return _bind(
        selected,
        node,
        task,
        checkout,
        result,
        apply=apply,
        revising_from=revising_from,
        earlier_runs=earlier_runs,
    )


def previous_attempt(
    config: HirakuConfig, node_id: str, *, scan: int = 200, limit: int = 3000
) -> dict[str, Any] | None:
    """The newest logged `implement` call on `node_id`: how it ended, what it said
    (its error, or the `notes`/`blocked` of its reply, cut to `limit` chars), and its
    last steps (tool calls and messages) from the logged transcript, if it streamed."""
    for meta in read_logs(config, limit=scan):
        if meta.get("workflow") != "implement" or meta.get("target") != node_id:
            continue
        out: dict[str, Any] = {
            "started": meta.get("started"),
            "seconds": meta.get("seconds"),
            "log": str(meta["path"]),
        }
        steps = transcript_steps(meta["path"] / TRANSCRIPT_NAME)
        if steps:
            out["last_steps"] = steps
        if meta.get("error"):
            out["outcome"] = "failed"
            out["error"] = str(meta["error"])[:limit]
            return out
        out["outcome"] = "blocked" if meta.get("blocked") else "replied"
        reply = meta["path"] / "reply.txt"
        try:
            data = extract_json(reply.read_text(encoding="utf-8"), ("notes", "commit"))
        except (OSError, AgentError):
            data = reply_from_transcript(
                meta["path"] / TRANSCRIPT_NAME,
                lambda found: ImplementReply.check(found, workflow="implement"),
                ImplementReply.model_fields,
            )
            if data is None:
                return out
        if isinstance(data, dict):
            for key in ("blocked", "notes", "commit"):
                if data.get(key):
                    out[key] = str(data[key])[:limit]
        return out
    return None


def _bound_commit(checkout: Checkout, reported: str) -> str:
    """The commit to bind: the node branch's new head, which must be what the agent
    reported and must descend from where the branch started.

    `HEAD` in the reply means the worktree's HEAD. A head that did not move, a reply
    that names something other than the branch head, or a branch rewritten to drop its
    old head are all refused: binding any of them would claim code this call did not
    write.
    """
    head = checkout.head()
    resolved = checkout.resolve(reported)
    if resolved is None:
        raise AgentError(
            f"The agent reported commit {reported!r}, which does not exist."
        )
    branch = checkout.branch
    if head == checkout.start:
        raise AgentError(
            f"The agent reported commit {reported!r} ({resolved[:12]}), but that "
            f"commit already existed before this call: branch {branch!r} is still at "
            f"{checkout.start[:12]}, so nothing was committed for this node."
        )
    if resolved != head:
        raise AgentError(
            f"The agent reported commit {resolved[:12]}, but branch {branch!r} is at "
            f"{head[:12]}. Check `git log {branch}`."
        )
    if not checkout.descends_from_start(head):
        raise AgentError(
            f"Branch {checkout.branch!r} was rewritten during the call: its new head "
            f"{head[:12]} does not descend from {checkout.start[:12]}."
        )
    return head


# ------------------------------------------------------------------------------- triage
