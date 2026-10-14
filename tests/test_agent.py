from __future__ import annotations

from pathlib import Path

import pytest

from hiraku import agent
from hiraku.agent import Context, list_contexts, load_context
from hiraku.config import HirakuConfig


def test_context_registry(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    context = Context(id="literature", markdown="Sources")
    context.save(config.contexts_dir / "literature/context.md")
    assert load_context("literature", config=config).markdown == "Sources"
    assert [item.id for item in list_contexts(config=config)] == ["literature"]


@pytest.mark.parametrize(
    "name", ["Program", "Action", "list_programs", "load_program", "load_action"]
)
def test_programs_and_actions_are_removed(name: str) -> None:
    assert not hasattr(agent, name)
