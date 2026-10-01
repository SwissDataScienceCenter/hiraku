from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from hiraku.agent.action import action_origin, load_action
from hiraku.agent.context import Context, load_context
from hiraku.agent.program import list_programs
from hiraku.cli import main
from hiraku.config import HirakuConfig


def test_agent_record_registries(tmp_path: Path) -> None:
    config = HirakuConfig.init(tmp_path)
    assert [record.id for record in list_programs(config=config)] == [
        "autoresearch",
        "interactive",
    ]
    context = Context(id="literature", markdown="Sources")
    context.save(config.contexts_dir / "literature/context.md")
    assert load_context("literature", config=config).markdown == "Sources"
    action = load_action("draft", entity="experiment", config=config)
    assert action.entity == "experiment"
    assert action_origin(action, config=config) == "bundled"
    assert not (config.actions_dir / "experiment/draft/action.md").exists()


def test_bundled_action_cannot_be_edited_or_deleted(
    tmp_path: Path, monkeypatch
) -> None:
    config = HirakuConfig.init(tmp_path, install_skill=False)
    monkeypatch.chdir(tmp_path)
    for command in ("update", "edit", "delete"):
        result = CliRunner().invoke(main, ["action", command, "experiment/draft"])
        assert result.exit_code != 0
        assert "bundled" in result.output
    assert load_action("draft", entity="experiment", config=config).id == "draft"
