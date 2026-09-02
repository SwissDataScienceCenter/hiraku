"""Conservative Git inspection, branch, and temporary-worktree services."""

from __future__ import annotations

import contextlib
import subprocess
import tempfile
from collections.abc import Iterator, Sequence
from pathlib import Path

from .config import HirakuConfig
from .errors import GitError, ValidationError


class GitRepository:
    """The Git repository containing a Hiraku project."""

    def __init__(self, config: HirakuConfig | Path | str | None = None) -> None:
        if isinstance(config, HirakuConfig):
            start = config.root
            self.config = config
        else:
            start = Path.cwd() if config is None else Path(config)
            self.config = None
        result = self._execute(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"], cwd=None
        )
        self.root = Path(result.stdout.strip()).resolve()
        project_root = (
            self.config.root.resolve() if self.config else Path(start).resolve()
        )
        try:
            project_root.relative_to(self.root)
        except ValueError as exc:
            raise GitError(
                f"Project {project_root} is outside Git repository {self.root}"
            ) from exc

    @staticmethod
    def _execute(
        arguments: Sequence[str], *, cwd: Path | None, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                list(arguments), cwd=cwd, text=True, capture_output=True, check=False
            )
        except OSError as exc:
            raise GitError(
                f"Could not execute Git command {list(arguments)!r}: {exc}"
            ) from exc
        if check and result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise GitError(f"Git command failed ({' '.join(arguments)}): {detail}")
        return result

    def _git(
        self, *arguments: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return self._execute(["git", *arguments], cwd=self.root, check=check)

    def resolve(self, ref: str) -> str:
        if not isinstance(ref, str) or not ref:
            raise GitError(f"Invalid Git ref: {ref!r}")
        return self._git("rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()

    @property
    def head(self) -> str:
        return self.resolve("HEAD")

    @property
    def current_branch(self) -> str | None:
        result = self._git("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    def validate_branch(self, branch: str) -> None:
        result = self._git("check-ref-format", "--branch", branch, check=False)
        if result.returncode != 0:
            raise ValidationError(f"Invalid Git branch name: {branch!r}")

    def branch_exists(self, branch: str) -> bool:
        self.validate_branch(branch)
        return (
            self._git(
                "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False
            ).returncode
            == 0
        )

    def create_branch(
        self, branch: str, *, start_ref: str = "HEAD", switch: bool = False
    ) -> str:
        self.validate_branch(branch)
        start = self.resolve(start_ref)
        if self.branch_exists(branch):
            raise GitError(f"Git branch already exists: {branch}")
        self._git("branch", branch, start)
        if switch:
            self.switch(branch)
        return branch

    def switch(self, branch: str) -> None:
        if not self.branch_exists(branch):
            raise GitError(f"Git branch does not exist: {branch}")
        self._git("switch", branch)

    def task_branch_name(self, task_id: str) -> str:
        if self.config is None:
            raise GitError("A Hiraku configuration is required for task branches")
        return self.config.task_branch.format(task_id=task_id)

    def experiment_branch_name(self, task_id: str, experiment_id: str) -> str:
        if self.config is None:
            raise GitError("A Hiraku configuration is required for experiment branches")
        return self.config.experiment_branch.format(
            task_id=task_id, experiment_id=experiment_id
        )

    def create_task_branch(
        self,
        task_id: str,
        *,
        branch: str | None = None,
        start_ref: str = "HEAD",
        switch: bool = False,
    ) -> str:
        selected = self.task_branch_name(task_id) if branch is None else branch
        if self.branch_exists(selected):
            if switch:
                self.switch(selected)
            return selected
        return self.create_branch(selected, start_ref=start_ref, switch=switch)

    def create_experiment_branch(
        self,
        task_id: str,
        experiment_id: str,
        *,
        branch: str | None = None,
        start_ref: str | None = None,
        switch: bool = False,
    ) -> str:
        selected = (
            self.experiment_branch_name(task_id, experiment_id)
            if branch is None
            else branch
        )
        if self.branch_exists(selected):
            if switch:
                self.switch(selected)
            return selected
        task_branch = self.task_branch_name(task_id)
        source = start_ref or task_branch
        return self.create_branch(
            selected,
            start_ref=source,
            switch=switch,
        )

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        first, second = self.resolve(ancestor), self.resolve(descendant)
        return (
            self._git(
                "merge-base", "--is-ancestor", first, second, check=False
            ).returncode
            == 0
        )

    def parent_experiments(
        self, experiment: object, experiments: Sequence[object]
    ) -> list[object]:
        commit = getattr(experiment, "commit", None)
        if commit is None:
            return []
        candidates = [
            item
            for item in experiments
            if item is not experiment
            and getattr(item, "commit", None)
            and self.is_ancestor(item.commit, commit)
        ]
        return [
            item
            for item in candidates
            if not any(
                other is not item
                and self.is_ancestor(item.commit, other.commit)
                and self.is_ancestor(other.commit, commit)
                for other in candidates
            )
        ]

    def child_experiments(
        self, experiment: object, experiments: Sequence[object]
    ) -> list[object]:
        commit = getattr(experiment, "commit", None)
        if commit is None:
            return []
        candidates = [
            item
            for item in experiments
            if item is not experiment
            and getattr(item, "commit", None)
            and self.is_ancestor(commit, item.commit)
        ]
        return [
            item
            for item in candidates
            if not any(
                other is not item
                and self.is_ancestor(commit, other.commit)
                and self.is_ancestor(other.commit, item.commit)
                for other in candidates
            )
        ]

    @contextlib.contextmanager
    def temporary_worktree(self, ref: str) -> Iterator[Path]:
        commit = self.resolve(ref)
        parent = Path(tempfile.mkdtemp(prefix="hiraku-worktree-"))
        worktree = parent / "checkout"
        added = False
        try:
            self._git("worktree", "add", "--detach", str(worktree), commit)
            added = True
            yield worktree
        finally:
            if added:
                self._git("worktree", "remove", "--force", str(worktree), check=False)
            self._git("worktree", "prune", check=False)
            with contextlib.suppress(OSError):
                worktree.rmdir()
            with contextlib.suppress(OSError):
                parent.rmdir()
