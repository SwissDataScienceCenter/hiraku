"""A dedicated git worktree for `implement`, so the agent never edits the user's tree.

`implement` is the one workflow that hands an agent write access and a shell. Running it
in the project's own working tree meant it could switch the user's branch, trip over
their uncommitted work, and — on a timeout — leave half-written files mixed in with
theirs. Instead each call gets its own worktree, checked out on the node's own branch
(created from the task's branch the first time). The worktrees live in
`<repository>/.hiraku-worktrees/`, listed in the repository's local exclude file
(`.git/info/exclude`) so they show up in nobody's `git status`. Never under `.git/`:
Claude Code treats everything there as sensitive and refuses to write it in a
non-interactive call, which would leave the agent unable to create a single file.
`HIRAKU_AGENT_WORKTREE_DIR` moves them elsewhere (outside `.git/` too). The agent
commits there; the code binds the branch head. A worktree with nothing left in it is
removed afterwards; one holding uncommitted edits or a failed attempt is kept, and its
path is reported, so nothing is discarded.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from hiraku.config import HirakuConfig
from hiraku.record import Record, load_record

from .errors import AgentError
from .lock import git_lock

WORKTREE_DIR_ENV_VAR = "HIRAKU_AGENT_WORKTREE_DIR"
WORKTREE_DIRNAME = ".hiraku-worktrees"
# The agent's running log, at the top of its worktree. Never committed (local exclude);
# when a call is stopped it is what the researcher and the next `--resume` read.
PROGRESS_FILE = ".hiraku-progress.md"


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


def project_state_paths(config: HirakuConfig, repository: Path) -> tuple[str, ...]:
    """Paths (relative to the repository root, `/`-separated) of hiraku's own project
    state: `hiraku.yml`, the records, contexts, programs, actions, agent defaults and
    agent logs. They live in the researcher's project directory, outside git history,
    so a copy of them in an agent's worktree is bookkeeping, never work to keep."""
    root = config.root.resolve()
    state = config.hiraku_dir.resolve()
    base = root.relative_to(repository.resolve())
    names = [
        root / "hiraku.yml",
        state / "agent.yaml",
        state / "records",
        state / "contexts",
        state / "programs",
        state / "actions",
        state / "agent-logs",
    ]
    return tuple((base / name.relative_to(root)).as_posix() for name in names)


def file_at_commit(
    repository: Path, config: HirakuConfig, commit: str, path: str
) -> str | None:
    """Text of `path` (relative to the project directory) in `commit`, or `None` if
    the commit has no such file."""
    prefix = config.root.resolve().relative_to(repository.resolve()).as_posix()
    full = path if prefix in ("", ".") else f"{prefix}/{path}"
    completed = subprocess.run(
        ["git", "-C", str(repository), "show", f"{commit}:{full}"],
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
        timeout=120,
    )
    return completed.stdout if completed.returncode == 0 else None


def dirty_lines(root: Path, ignore: tuple[str, ...] = ()) -> list[str]:
    """`git status --porcelain` lines of the worktree at `root`, one per file, without
    those under `ignore` (paths relative to `root`)."""
    completed = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "-uall"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        return []
    kept: list[str] = []
    for line in completed.stdout.splitlines():
        path = line[3:].split(" -> ")[-1].strip().strip('"')
        if any(
            path == item or path.startswith(item.rstrip("/") + "/") for item in ignore
        ):
            continue
        kept.append(line)
    return kept


@dataclass
class Checkout:
    """One `implement` call's worktree: where the agent runs, and on which branch."""

    root: Path  # the worktree's top level
    cwd: Path  # the hiraku project inside it (the project may be a repo subdirectory)
    branch: str
    start: str  # the branch head before the agent ran (the branch's base on --resume)
    created_branch: bool
    repository: Path  # the user's repository top level
    resumed: bool = False  # continuing an earlier attempt's worktree
    config: HirakuConfig | None = None  # for the repository lock in `finish`

    def head(self) -> str:
        head = _resolve(self.repository, f"refs/heads/{self.branch}")
        if head is None:
            raise AgentError(f"Branch {self.branch!r} disappeared during the call.")
        return head

    def moved(self) -> bool:
        """Whether the branch has commits beyond `start` (False if it is gone)."""
        head = _resolve(self.repository, f"refs/heads/{self.branch}")
        return head is not None and head != self.start

    def resolve(self, ref: str) -> str | None:
        """`ref` as the agent saw it: `HEAD` means the worktree's HEAD."""
        return _resolve(self.root, ref)

    def _state_paths(self) -> tuple[str, ...]:
        if self.config is None:
            return ()
        return project_state_paths(self.config, self.repository)

    def status(self) -> str:
        """The worktree's uncommitted changes, not counting hiraku's own project state
        (a copy of `hiraku.yml`, records and the like is not work to keep)."""
        return "\n".join(dirty_lines(self.root, self._state_paths()))

    def file_at(self, commit: str, path: str) -> str | None:
        """Text of `path` (relative to the project directory) in `commit`, or `None`
        if the commit has no such file."""
        prefix = self.cwd.resolve().relative_to(self.root.resolve()).as_posix()
        full = path if prefix in ("", ".") else f"{prefix}/{path}"
        completed = subprocess.run(
            ["git", "-C", str(self.root), "show", f"{commit}:{full}"],
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            timeout=120,
        )
        return completed.stdout if completed.returncode == 0 else None

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

    def progress(self) -> str:
        """The agent's progress file, or "" if it wrote none."""
        try:
            return (self.root / PROGRESS_FILE).read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def finish(self, *, keep_progress: bool = False) -> bool:
        """Remove the worktree if it holds nothing worth keeping; return True if kept.

        Kept when it has uncommitted changes, or, with `keep_progress` (a call that
        did not finish), when the agent left a progress file. A branch this call
        created and the agent never committed to is deleted again, so a blocked or
        failed call leaves no trace in the repository.
        """
        if self.status() or (keep_progress and self.progress()):
            return True
        # What is left is only project state copied in: the researcher's own copies stay
        # where they are, so removing this one needs `--force` (git refuses otherwise).
        forced = ["--force"] if dirty_lines(self.root) else []
        with git_lock(self.config) if self.config else contextlib.nullcontext():
            _git(
                self.repository,
                "worktree",
                "remove",
                *forced,
                str(self.root),
                check=False,
            )
            _git(self.repository, "worktree", "prune", check=False)
            if self.created_branch and not self.moved():
                _git(self.repository, "branch", "-D", self.branch, check=False)
        return False


class ProgressWatcher:
    """Reports the lines the agent appends to its progress file while it runs.

    Starts after what is already there (a resumed attempt's notes are not repeated).
    Only whole lines are reported; a rewritten (shorter) file is read from the top.
    """

    def __init__(self, path: Path, emit: Callable[[str], None]) -> None:
        self.path, self.emit = path, emit
        self.offset = self._size()

    def _size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def poll(self) -> None:
        size = self._size()
        if size < self.offset:
            self.offset = 0
        if size == self.offset:
            return
        try:
            with self.path.open("rb") as handle:
                handle.seek(self.offset)
                chunk = handle.read(size - self.offset)
        except OSError:
            return
        end = chunk.rfind(b"\n")
        if end == -1:
            return  # wait for the line to be finished
        self.offset += end + 1
        for line in chunk[: end + 1].decode("utf-8", "replace").splitlines():
            if line.strip():
                self.emit(line.rstrip())


def repository_root(config: HirakuConfig) -> Path | None:
    """The git top level containing the project, or None when it is not in git."""
    top = _git(config.root, "rev-parse", "--show-toplevel", check=False)
    return Path(top) if top else None


def worktree_parent(repository: Path) -> Path:
    """Where `implement` worktrees go: `HIRAKU_AGENT_WORKTREE_DIR`, or
    `<repository>/.hiraku-worktrees` (kept out of `git status` through the repository's
    local exclude file). Refuses anything under a `.git` directory, where the agent
    would not be allowed to write."""
    configured = os.environ.get(WORKTREE_DIR_ENV_VAR)
    parent = (
        Path(configured).expanduser() if configured else repository / WORKTREE_DIRNAME
    )
    if not parent.is_absolute():
        parent = repository / parent
    parent = parent.resolve()
    if ".git" in parent.parts:
        raise AgentError(
            f"Worktree directory {parent} is inside a .git directory, where the agent "
            f"may not write files. Point {WORKTREE_DIR_ENV_VAR} somewhere else."
        )
    parent.mkdir(parents=True, exist_ok=True)
    if not configured:
        _exclude(repository, f"/{WORKTREE_DIRNAME}/")
    _exclude(repository, f"/{PROGRESS_FILE}")
    return parent


def _exclude(repository: Path, pattern: str) -> None:
    """Add `pattern` to the repository's local exclude file once (never `.gitignore`,
    which is tracked and shared)."""
    path = Path(_git(repository, "rev-parse", "--git-path", "info/exclude"))
    if not path.is_absolute():
        path = repository / path
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    if pattern in (line.strip() for line in lines):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        if lines and lines[-1].strip():
            stream.write("\n")
        stream.write(f"# hiraku-agent implement\n{pattern}\n")


def _branch_base(
    config: HirakuConfig, repository: Path, node: Record, task: Record
) -> str:
    """Where a new node branch starts.

    A node under another node builds on it (a comparison of methods reuses the
    baseline's harness; a diagnostic inspects its parent's code), so it starts at the
    commit its parent is bound to, and cannot start before the parent has one. A node
    directly under the task starts at the task's branch when that exists, else at HEAD.
    """
    if node.parent_id and node.parent_id != task.id:
        parent = load_record(node.parent_id, config=config)
        if not parent.commit:
            raise AgentError(
                f"'{node.id}' builds on '{parent.id}', which has no code yet. "
                f"Implement '{parent.id}' first (`implement --all` goes parents "
                f"first)."
            )
        return parent.commit
    if task.branch and _resolve(repository, f"refs/heads/{task.branch}"):
        return task.branch
    return "HEAD"


def _branch_name(config: HirakuConfig, node: Record) -> str:
    return node.branch or config.branch.format(record_id=node.id)


def _worktree_for_branch(repository: Path, branch: str) -> Path | None:
    """The worktree that has `branch` checked out, if any (git allows one)."""
    listing = _git(repository, "worktree", "list", "--porcelain", check=False)
    path: Path | None = None
    for line in listing.splitlines():
        if line.startswith("worktree "):
            path = Path(line[len("worktree ") :])
        elif line.strip() == f"branch refs/heads/{branch}" and path is not None:
            return path
    return None


def _fork_point(
    config: HirakuConfig, repository: Path, node: Record, task: Record, branch: str
) -> str:
    """Where the node's branch left its base; everything after it is the node's."""
    base = _branch_base(config, repository, node, task)
    fork = _git(repository, "merge-base", f"refs/heads/{branch}", base, check=False)
    return fork or _resolve(repository, f"refs/heads/{branch}") or base


def resume_state(
    config: HirakuConfig, node: Record, task: Record
) -> dict[str, object] | None:
    """What an earlier attempt left for `node`: its kept worktree (if any), the commits
    on the node branch since its base, and the uncommitted changes. Read-only; None
    when there is nothing to continue."""
    repository = repository_root(config)
    if repository is None:
        return None
    branch = _branch_name(config, node)
    if _resolve(repository, f"refs/heads/{branch}") is None:
        return None
    kept = _worktree_for_branch(repository, branch)
    if kept is not None and kept.resolve() == repository.resolve():
        kept = None  # the user's own tree: never resumed into
    try:
        fork = _fork_point(config, repository, node, task, branch)
    except AgentError:
        return None
    commits = _git(
        repository,
        "log",
        "--oneline",
        "-n",
        "30",
        f"{fork}..refs/heads/{branch}",
        check=False,
    )
    uncommitted = (
        "\n".join(dirty_lines(kept, project_state_paths(config, repository)))
        if kept
        else ""
    )
    progress = ""
    if kept is not None:
        try:
            progress = (kept / PROGRESS_FILE).read_text(encoding="utf-8").strip()
        except OSError:
            progress = ""
    if not commits and not uncommitted and not progress:
        return None
    return {
        "worktree": str(kept) if kept else None,
        "branch": branch,
        "base": fork,
        "commits_since_base": commits.splitlines(),
        "uncommitted": uncommitted.splitlines()[:100],
        "progress": progress[-6000:],
    }


def revise_state(config: HirakuConfig, node: Record) -> dict[str, object]:
    """What a revision of `node` starts from: its bound commit, and anything an
    earlier, stopped revision left (commits after it, a kept worktree). Read-only."""
    out: dict[str, object] = {"bound_commit": node.commit}
    repository = repository_root(config)
    if repository is None or not node.commit:
        return out
    branch = _branch_name(config, node)
    out["branch"] = branch
    if _resolve(repository, f"refs/heads/{branch}") is None:
        return out
    commits = _git(
        repository,
        "log",
        "--oneline",
        "-n",
        "30",
        f"{node.commit}..refs/heads/{branch}",
        check=False,
    )
    out["commits_since_bound"] = commits.splitlines()
    kept = _worktree_for_branch(repository, branch)
    if kept is not None and kept.resolve() != repository.resolve():
        out["worktree"] = str(kept)
        out["uncommitted"] = dirty_lines(kept, project_state_paths(config, repository))[
            :100
        ]
        try:
            progress = (kept / PROGRESS_FILE).read_text(encoding="utf-8").strip()
        except OSError:
            progress = ""
        if progress:
            out["progress"] = progress[-6000:]
    return out


def open_checkout(
    config: HirakuConfig,
    node: Record,
    task: Record,
    *,
    resume: bool = False,
    start_at: str | None = None,
) -> Checkout:
    """`_open_checkout` under the repository lock, so parallel `implement` calls do
    not trip over each other's branch and worktree changes."""
    with git_lock(config):
        checkout = _open_checkout(config, node, task, resume=resume, start_at=start_at)
    checkout.config = config
    return checkout


def _open_checkout(
    config: HirakuConfig,
    node: Record,
    task: Record,
    *,
    resume: bool = False,
    start_at: str | None = None,
) -> Checkout:
    """Create a worktree on `node`'s branch, creating the branch from the task's.

    `start_at` (`implement --revise`): the branch must already exist and contain that
    commit (the node's bound one); work counts from there, and a worktree an earlier
    revise left behind is reused.

    The branch is the node's own `branch` field if set, otherwise the project's branch
    pattern for the node id (`hiraku.yml`'s `branch`). A new branch starts at the
    parent node's bound commit, or for a node directly under the task at the task's
    branch when that exists, else at the current HEAD (`_branch_base`).
    """
    repository = repository_root(config)
    if repository is None:
        raise AgentError("The project is not inside a git repository.")
    # Before the branch is created, so a refused location leaves nothing behind.
    parent = worktree_parent(repository)
    branch = _branch_name(config, node)
    _git(repository, "check-ref-format", "--branch", branch)
    created = _resolve(repository, f"refs/heads/{branch}") is None
    relative = config.root.resolve().relative_to(repository.resolve())
    if start_at is not None:
        if created:
            raise AgentError(
                f"Branch {branch!r} of '{node.id}' no longer exists, so its code "
                f"cannot be revised. Recreate it at {start_at[:12]} "
                f"(`git branch {branch} {start_at}`) and run this again."
            )
        inside = subprocess.run(
            ["git", "merge-base", "--is-ancestor", start_at, f"refs/heads/{branch}"],
            cwd=repository,
            capture_output=True,
            check=False,
        )
        if inside.returncode != 0:
            raise AgentError(
                f"Branch {branch!r} no longer contains '{node.id}''s bound commit "
                f"{start_at[:12]}, so a revision could not build on it. Check "
                f"`git log {branch}`."
            )
    kept = None if created else _worktree_for_branch(repository, branch)
    if kept is not None and kept.resolve() != repository.resolve():
        if start_at is not None:
            return Checkout(
                root=kept,
                cwd=kept / relative,
                branch=branch,
                start=start_at,
                created_branch=False,
                repository=repository,
                resumed=True,
            )
        if not resume:
            left = _git(kept, "status", "--short", check=False)
            raise AgentError(
                f"An earlier implement attempt for '{node.id}' is kept at {kept}"
                + (
                    f" with {len(left.splitlines())} uncommitted change(s)"
                    if left
                    else ""
                )
                + f". Continue it with `hiraku-agent implement {node.id} --resume`, "
                f"or start over after `git worktree remove --force {kept}`."
            )
        return Checkout(
            root=kept,
            cwd=kept / relative,
            branch=branch,
            start=_fork_point(config, repository, node, task, branch),
            created_branch=False,
            repository=repository,
            resumed=True,
        )
    if created:
        base = _branch_base(config, repository, node, task)
        if _resolve(repository, base) is None:
            raise AgentError(
                "The repository has no commit to start the node's branch from; make an "
                "initial commit first."
            )
        _git(repository, "branch", branch, base)
    start = _resolve(repository, f"refs/heads/{branch}")
    assert start is not None
    if start_at is not None:
        start = start_at
    elif resume and not created:
        # Commits an earlier, stopped attempt left on the branch count as this node's.
        start = _fork_point(config, repository, node, task, branch)
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
    return Checkout(
        root=root,
        cwd=root / relative,
        branch=branch,
        start=start,
        created_branch=created,
        repository=repository,
        resumed=resume and not created,
    )
