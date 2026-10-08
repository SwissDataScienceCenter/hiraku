"""`implement`: the one workflow where the agent writes code."""

from __future__ import annotations

from hiraku.config import HirakuConfig
from hiraku.errors import HirakuError

from .. import sections
from ..engine import (
    DEFAULT_IMPLEMENT_AGENT_CMD,
    IMPLEMENT_AGENT_CMD_ENV_VAR,
    AgentRun,
    Hooks,
    agent_command,
)
from ..errors import AgentError, AgentTimeout
from ..gates import is_approved
from ..lock import writes
from ..node import (
    list_nodes,
    load_node,
    task_of,
)
from ..replies import ImplementReply
from ..worktree import Checkout, open_checkout, repository_root
from ._common import AgentCall, task_state

# -------------------------------------------------------------------------- implement


@writes("implement")
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
    """
    selected = config or HirakuConfig.discover()
    node = load_node(node_id, task=task_id, config=selected)
    if node.abandoned:
        raise HirakuError(f"'{node.id}' is abandoned — nothing to implement.")
    if not node.is_proposal:
        raise HirakuError(
            f"'{node.id}' already has a commit bound ({node.commit}) — nothing to "
            f"implement. Use `hiraku record update` directly if you meant to "
            f"rebind it."
        )
    if not is_approved(node):
        raise AgentError(
            f"'{node.id}' is not approved yet. Run `hiraku-agent approve {node.id} "
            f"--task {task_of(node, config=selected).id} --by <you>` first — in guided "
            f"mode that approval is what authorizes this workflow to write real code."
        )

    task = task_of(node, config=selected)
    nodes = list_nodes(task=task, config=selected)
    # No `repo_paths`: `--repo` grants read-only tools, and this workflow's command
    # needs write access, so the two are not combined.
    call = AgentCall.resolve(
        selected,
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
    result = call.prepare(
        "implement",
        task_state(task, nodes, config=selected, focus=node.id),
        instruction=instruction,
    )
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
    checkout = open_checkout(selected, node, task)
    try:
        call.ask(result, node.id, reply=ImplementReply, cwd=checkout.cwd)
    except Exception as exc:
        # Stopped mid-edit, or failed: nothing is bound and nothing is discarded.
        kept = checkout.finish()
        left = checkout.status() if kept else ""
        detail = (
            f"The agent's uncommitted changes are kept in {checkout.root}:\n{left}"
            if kept
            else "It left no uncommitted changes."
        )
        message = f"{exc}\n'{node.id}' was not bound to any commit. {detail}"
        if isinstance(exc, AgentTimeout):
            raise AgentTimeout(message) from exc
        if isinstance(exc, AgentError):
            raise AgentError(message) from exc
        raise
    data = result.data
    if data.get("blocked") or not apply:
        kept = checkout.finish()
        if kept:
            data["worktree_kept"] = str(checkout.root)
        return result

    try:
        resolved_commit = _bound_commit(checkout, str(data["commit"]))
    except AgentError as exc:
        kept = checkout.finish()
        where = f" The worktree is kept at {checkout.root}." if kept else ""
        raise AgentError(f"{exc} '{node.id}' was not bound.{where}") from exc

    # Reload: an agent call that just edited and committed files is exactly the case
    # `run_triage`/`run_analyze`'s own "an agent call is minutes long" reload guards
    # against, and here the stakes are higher — binding a commit is the one write in
    # this module that is not easily reverted by editing a markdown file back.
    live = load_node(node.id, task=task, config=selected)
    if not live.is_proposal:
        raise HirakuError(
            f"'{live.id}' already has a commit bound ({live.commit}) — something else "
            f"implemented it while this call was running. Not overwriting; inspect "
            f"both commits by hand."
        )

    entrypoints = data.get("entrypoints")
    if isinstance(entrypoints, dict) and entrypoints:
        live.entrypoints = {**live.entrypoints, **entrypoints}

    if data.get("notes"):
        live.markdown = sections.set_section(
            live.markdown, "Implementation notes", str(data["notes"])
        )

    live.commit = resolved_commit
    if not live.branch:
        live.branch = checkout.branch
    live.save()
    result.written.append(live.path)
    if checkout.finish():
        data["worktree_kept"] = str(checkout.root)
        data["notes"] = (
            f"{data.get('notes') or ''}\n\nThe agent left uncommitted changes, kept in "
            f"{checkout.root}; they are not part of the bound commit."
        ).strip()
    return result


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
