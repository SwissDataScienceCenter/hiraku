from __future__ import annotations

from pathlib import Path

from hiraku.core.agent import Context, list_programs, load_action, load_context
from hiraku.core.config import HirakuConfig


def test_agent_record_registries(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    assert [record.id for record in list_programs(config=config)] == [
        "autoresearch",
        "interactive",
        "literature-review",
    ]
    context = Context(id="literature", markdown="Sources")
    context.save(config.contexts_dir / "literature/context.md")
    assert load_context("literature", config=config).markdown == "Sources"
    action = load_action("propose", entity="record", config=config)
    assert action.entity == "record"
