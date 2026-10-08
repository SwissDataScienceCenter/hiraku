"""A dedicated git worktree for `implement`, so the agent never edits the user's tree.

`implement` is the one workflow that hands an agent write access and a shell. Running it
in the project's own working tree meant it could switch the user's branch, trip over
their uncommitted work, and — on a timeout — leave half-written files mixed in with
theirs. Instead each call gets its own worktree, checked out on the node's own branch
(created from the task's branch the first time), under the git directory where it
shows up in nobody's `git status`. The agent commits there; the code binds the branch
head. A worktree with nothing left in it is removed afterwards; one holding uncommitted
edits or a failed attempt is kept, and its path is reported, so nothing is discarded.
"""

from __future__ import annotations

import datetime as _dt
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from hiraku.config import HirakuConfig
from hiraku.record import Record

from .errors import AgentError

WORKTREE_DIR_ENV_VAR = "HIRAKU_AGENT_WORKTREE_DIR"


def _git(cwd: Path, *arguments: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if check and completed.returncode != 0:
        raise AgentError(
            f"git {' '.join(arguments)} failed: "
            f"{(completed.stderr or completed.stdout).strip()}"
        )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _resolve(cwd: Path, ref: str) -> str | None:
    return (
        _git(cwd, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
        or None
    )


@dataclass
class Checkout:
    """One `implement` call's worktree: where the agent runs, and on which branch."""

    root: Path  # the worktree's top level
    cwd: Path  # the hiraku project inside it (the project may be a repo subdirectory)
    branch: str
    start: str  # the branch head before the agent ran
    created_branch: bool
    repository: Path  # the user's repository top level

    def head(self) -> str:
        head = _resolve(self.repository, f"refs/heads/{self.branch}")
        if head is None:
            raise AgentError(f"Branch {self.branch!r} disappeared during the call.")
        return head

    def resolve(self, ref: str) -> str | None:
        """`ref` as the agent saw it: `HEAD` means the worktree's HEAD."""
        return _resolve(self.root, ref)

    def status(self) -> str:
        return _git(self.root, "status", "--short", check=False)

    def descends_from_start(self, commit: str) -> bool:
        """Is `commit` the branch's starting point or built on top of it?"""
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(self.repository),
                "merge-base",
                "--is-ancestor",
                self.start,
                commit,
            ],
            capture_output=True,
            check=False,
            timeout=120,
        )
        return completed.returncode == 0

    def finish(self) -> bool:
        """Remove the worktree if it holds nothing worth keeping; return True if kept.

        Kept when it has uncommitted changes. A branch this call created and the agent
        never committed to is deleted again, so a blocked or failed call leaves no
        trace in the repository.
        """
        if self.status():
            return True
        _git(self.repository, "worktree", "remove", str(self.root), check=False)
        _git(self.repository, "worktree", "prune", check=False)
        if self.created_branch and self.head() == self.start:
            _git(self.repository, "branch", "-D", self.branch, check=False)
        return False


def repository_root(config: HirakuConfig) -> Path | None:
    """The git top level containing the project, or None when it is not in git."""
    top = _git(config.root, "rev-parse", "--show-toplevel", check=False)
    return Path(top) if top else None


def open_checkout(config: HirakuConfig, node: Record, task: Record) -> Checkout:
    """Create a worktree on `node`'s branch, creating the branch from the task's.

    The branch is the node's own `branch` field if set, otherwise the project's branch
    pattern for the node id (`hiraku.yml`'s `branch`). A new branch starts at the task's
    branch when that exists, else at the current HEAD.
    """
    repository = repository_root(config)
    if repository is None:
        raise AgentError("The project is not inside a git repository.")
    branch = node.branch or config.branch.format(record_id=node.id)
    _git(repository, "check-ref-format", "--branch", branch)
    created = _resolve(repository, f"refs/heads/{branch}") is None
    if created:
        base = "HEAD"
        if task.branch and _resolve(repository, f"refs/heads/{task.branch}"):
            base = task.branch
        if _resolve(repository, base) is None:
            raise AgentError(
                "The repository has no commit to start the node's branch from; make an "
                "initial commit first."
            )
        _git(repository, "branch", branch, base)
    start = _resolve(repository, f"refs/heads/{branch}")
    assert start is not None
    # `--git-common-dir` may print a path relative to the directory git ran in (the
    # repository), never to this process's cwd: anchor it there.
    configured = os.environ.get(WORKTREE_DIR_ENV_VAR)
    common = Path(_git(repository, "rev-parse", "--git-common-dir"))
    parent = Path(configured) if configured else common / "hiraku-worktrees"
    if not parent.is_absolute():
        parent = (repository / parent).resolve()
    parent.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    root = parent / f"{node.id}-{stamp}"
    suffix = 1
    while root.exists():
        suffix += 1
        root = parent / f"{node.id}-{stamp}-{suffix}"
    added = subprocess.run(
        ["git", "-C", str(repository), "worktree", "add", "--quiet", str(root), branch],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    if added.returncode != 0:
        if created:
            _git(repository, "branch", "-D", branch, check=False)
        raise AgentError(
            f"Could not check out {branch!r} in a separate worktree: "
            f"{added.stderr.strip()}. If it is checked out in your own working tree, "
            "switch away from it first, so the agent cannot edit your files."
        )
    relative = config.root.resolve().relative_to(repository.resolve())
    return Checkout(
        root=root,
        cwd=root / relative,
        branch=branch,
        start=start,
        created_branch=created,
        repository=repository,
    )
