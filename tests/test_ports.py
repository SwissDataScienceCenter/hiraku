from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

from hiraku.application.ports import AgentAdapter, LauncherPort, SourceControl


class FakeAgent:
    def exchange(self, request):
        return {"request_id": request.request_id}


class FakeLauncher:
    def launch(self, target: str, commit_ref: str | None = None):
        return "R-fake", 0


class FakeSource:
    root = Path(".")

    def ensure_repository(self): ...
    def ensure_clean(self): ...
    def head_commit(self):
        return "abc"

    def resolve_commit(self, ref):
        return ref

    def is_ancestor(self, ancestor, descendant):
        return True

    def current_branch(self):
        return "main"

    def branch_exists(self, branch):
        return False

    def has_changes(self):
        return False

    def changed_paths(self):
        return set()

    def commit_all(self, message):
        return "abc"

    def create_and_checkout_branch(self, branch, start_commit):
        return "main"

    def rollback_created_branch(self, original_branch, created_branch): ...
    def detached_worktree(self, commit):
        return nullcontext(Path("."))


def test_adapters_are_structural_contracts():
    assert isinstance(FakeAgent(), AgentAdapter)
    assert isinstance(FakeLauncher(), LauncherPort)
    assert isinstance(FakeSource(), SourceControl)
