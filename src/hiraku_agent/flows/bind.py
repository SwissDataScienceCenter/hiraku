"""`bind`: bind a commit you made yourself to a node, with `implement`'s checks.

`implement` binds the commit its agent made. `bind` is for code you wrote (or had an
interactive session write) on the node's branch: the node is bound to the branch's
newest commit, or to `--commit REF`, after the same checks `implement` makes, and no
agent is called. `hiraku record update --commit` does not check anything; this does.
"""

from __future__ import annotations

import datetime as _dt
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hiraku.config import HirakuConfig
from hiraku.errors import HirakuError

from .. import sections
from ..entrypoints import entrypoint_problems
from ..errors import AgentError
from ..gates import is_approved
from ..lock import writes
from ..node import load_node, task_of
from ..worktree import (
    _branch_name,
    _fork_point,
    _git,
    _resolve,
    _worktree_for_branch,
    dirty_lines,
    file_at_commit,
    project_state_paths,
    repository_root,
)
from .implement import revision_runs


@dataclass
class BindPlan:
    """What `bind` would do, and what it found. Nothing is written to make one."""

    node_id: str
    branch: str
    commit: str  # the full hash to bind
    previous: str | None  # the commit the node is bound to now, if any
    commits: list[str] = field(
        default_factory=list
    )  # `git log --oneline`, newest first
    earlier_runs: list[dict[str, Any]] = field(default_factory=list)
    cautions: list[str] = field(default_factory=list)
    uncommitted: list[str] = field(default_factory=list)  # in the branch's worktree
    worktree: str | None = None
    bound: bool = False


def _is_ancestor(repository: Path, older: str, newer: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(repository), "merge-base", "--is-ancestor", older, newer],
            capture_output=True,
            check=False,
            timeout=120,
        ).returncode
        == 0
    )


def plan_bind(
    node_id: str,
    *,
    task_id: str | None = None,
    commit: str | None = None,
    keep_runs: bool = False,
    config: HirakuConfig | None = None,
) -> BindPlan:
    """Run every check `bind` makes, without writing. Raises `AgentError` when the
    commit must not be bound."""
    selected = config or HirakuConfig.discover()
    node = load_node(node_id, task=task_id, config=selected)
    if node.abandoned:
        raise HirakuError(f"'{node.id}' is abandoned — nothing to bind.")
    if not is_approved(node):
        raise AgentError(
            f"'{node.id}' is not approved yet. Run `hiraku-agent approve {node.id} "
            f"--by <you>` first."
        )
    repository = repository_root(selected)
    if repository is None:
        raise AgentError("`bind` needs the project to be in a git repository.")
    task = task_of(node, config=selected)
    branch = _branch_name(selected, node)
    head = _resolve(repository, f"refs/heads/{branch}")
    if head is None:
        raise AgentError(
            f"Branch {branch!r} of '{node.id}' does not exist, so there is nothing to "
            f"bind. Make your commit on that branch first."
        )

    resolved = _resolve(repository, commit) if commit else head
    if resolved is None:
        raise AgentError(f"Commit {commit!r} does not exist in this repository.")
    if not _is_ancestor(repository, resolved, f"refs/heads/{branch}"):
        raise AgentError(
            f"Commit {resolved[:12]} is not on branch {branch!r}: a node is bound "
            f"only to code on its own branch."
        )

    previous = node.commit or None
    if previous:
        if resolved == previous:
            raise AgentError(
                f"'{node.id}' is already bound to {previous[:12]}, and that is the "
                f"newest commit on {branch!r}; there is nothing new to bind."
                if resolved == head
                else f"'{node.id}' is already bound to {previous[:12]}."
            )
        if not _is_ancestor(repository, previous, resolved):
            raise AgentError(
                f"Commit {resolved[:12]} does not build on '{node.id}''s bound commit "
                f"{previous[:12]}: a revision must descend from it."
            )
        earlier = revision_runs(node, config=selected, keep_runs=keep_runs)
        since = previous
    else:
        earlier = []
        since = _fork_point(selected, repository, node, task, branch)
        if resolved == since or not _is_ancestor(repository, since, resolved):
            raise AgentError(
                f"Commit {resolved[:12]} is not new code for '{node.id}': it is not "
                f"after the point where the node's branch started ({since[:12]})."
            )

    log = _git(
        repository, "log", "--oneline", "-n", "15", f"{since}..{resolved}", check=False
    )

    problems, cautions = entrypoint_problems(
        dict(node.entrypoints or {}),
        read_file=lambda path: file_at_commit(repository, selected, resolved, path),
    )
    if problems:
        raise AgentError(
            "The entrypoints stored on the node would not run against this commit, so "
            "it was not bound: " + "; ".join(problems) + ". Fix the code, or the "
            f"entrypoints in the node's record (`hiraku record edit {node.id}`)."
        )

    plan = BindPlan(
        node_id=node.id,
        branch=branch,
        commit=resolved,
        previous=previous,
        commits=log.splitlines(),
        earlier_runs=earlier,
        cautions=cautions,
    )
    tree = _worktree_for_branch(repository, branch)
    if tree is not None:
        plan.worktree = str(tree)
        plan.uncommitted = dirty_lines(tree, project_state_paths(selected, repository))
    return plan


@writes("bind")
def run_bind(
    node_id: str,
    *,
    task_id: str | None = None,
    commit: str | None = None,
    keep_runs: bool = False,
    dry_run: bool = False,
    confirm: Callable[[BindPlan], bool] | None = None,
    config: HirakuConfig | None = None,
) -> BindPlan:
    """Bind the node's branch head (or `commit`) to the node, after the checks.

    `confirm`, if given, is asked once with the plan, before anything is written.
    """
    selected = config or HirakuConfig.discover()
    plan = plan_bind(
        node_id, task_id=task_id, commit=commit, keep_runs=keep_runs, config=selected
    )
    if dry_run:
        return plan
    if confirm is not None and not confirm(plan):
        raise AgentError("Not bound.")

    # Reload: the prompt to confirm can be a long wait, and binding is the one write
    # that is not easily reverted by editing a markdown file back.
    live = load_node(node_id, task=task_id, config=selected)
    if (live.commit or None) != plan.previous:
        raise AgentError(
            f"'{live.id}' was bound to {live.commit} while this call waited (it was "
            f"{plan.previous}). Not overwriting; run `bind` again."
        )
    stamp = _dt.date.today().isoformat()
    subjects = "; ".join(line.split(" ", 1)[-1] for line in plan.commits[:5])
    if plan.previous:
        entry = (
            f"**Revision {stamp}** ({plan.previous[:12]} -> {plan.commit[:12]}): bound "
            f"by hand with `hiraku-agent bind`. Commits: {subjects}."
        )
        if plan.earlier_runs:
            ids = ", ".join(str(run["id"]) for run in plan.earlier_runs[:10])
            entry += (
                f"\n\n{len(plan.earlier_runs)} earlier run(s) stay attributed to "
                f"{plan.previous[:12]} and were not produced by this revision: {ids}."
            )
    else:
        entry = (
            f"Bound by hand with `hiraku-agent bind` on {stamp}: {plan.commit[:12]} "
            f"on {plan.branch}. Commits: {subjects}."
        )
    live.markdown = sections.append_section(
        live.markdown, "Implementation notes", entry
    )
    live.commit = plan.commit
    if not live.branch:
        live.branch = plan.branch
    live.save()
    plan.bound = True
    return plan
