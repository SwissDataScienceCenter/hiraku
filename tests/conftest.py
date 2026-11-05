from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def disable_git_commit_signing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable commit signing in Git subprocesses started by tests."""
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "commit.gpgsign")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "false")
