from __future__ import annotations

from click.testing import CliRunner

from hiraku.cli import main
from hiraku.config import HirakuConfig


def test_init_uses_only_mcp_for_workflow_interaction(tmp_path) -> None:
    HirakuConfig.init(tmp_path, example="workflows", install_skill=False)

    assert not (tmp_path / ".codex/hooks.json").exists()
    assert not (tmp_path / ".claude/settings.json").exists()

    help_text = CliRunner().invoke(main, ["--help"]).output
    assert "session" not in help_text


def test_workflow_cli_exposes_langgraph_controls_only() -> None:
    help_text = CliRunner().invoke(main, ["workflow", "--help"]).output

    assert "list" in help_text
    assert "start" in help_text
    assert "status" in help_text
    assert "resume" in help_text
    assert "send-event" not in help_text
    assert "graph" not in help_text
