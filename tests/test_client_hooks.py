from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys

import pytest
from click.testing import CliRunner

from hiraku.cli import _inject_client, main
from hiraku.config import HirakuConfig


@pytest.mark.parametrize(
    "tool",
    ["count-steps", "count_steps", "session_list", "session_show", "session_cancel"],
)
def test_generated_codex_hook_runs_without_hiraku_on_path(tmp_path, tool):
    config = HirakuConfig.init(tmp_path, example="workflows", install_skill=False)
    hooks = json.loads((tmp_path / ".codex/hooks.json").read_text())
    entry = hooks["hooks"]["PreToolUse"][0]
    assert re.search(entry["matcher"], f"mcp__hiraku__{tool}")
    command = entry["hooks"][0]["command"]
    arguments = command if os.name == "nt" else shlex.split(command)
    result = subprocess.run(
        arguments,
        cwd=config.root,
        env={**os.environ, "PATH": ""},
        input=json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": f"mcp__hiraku__{tool}",
                "cwd": str(config.root),
                "session_id": "chat",
                "turn_id": "turn",
                "tool_input": {"count": 3},
            }
        ),
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stderr == ""
    if tool.startswith("session_"):
        assert json.loads(result.stdout) == {}
        return
    assert json.loads(result.stdout)["hookSpecificOutput"]["updatedInput"] == {
        "count": 3,
        "hiraku_client": {
            "client": "codex",
            "client_session_id": "chat",
            "client_turn_id": "turn",
        },
    }


def test_hook_command_quotes_python_path(tmp_path, monkeypatch):
    from hiraku.project import ProjectInitializer

    executable = str(tmp_path / "environment with spaces" / "python")
    monkeypatch.setattr(sys, "executable", executable)
    ProjectInitializer._install_client_hooks(tmp_path, [], [], {})
    hooks = json.loads((tmp_path / ".codex/hooks.json").read_text())
    command = hooks["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    arguments = [executable, "-m", "hiraku.cli", "session", "hook", "codex"]
    assert command == (
        subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)
    )


@pytest.mark.parametrize("client", ["codex", "claude"])
def test_hook_injects_identity_and_preserves_inputs(client):
    arguments = {"session_id": "workflow-123", "hiraku_client": {"client": "wrong"}}
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "mcp__hiraku__workflow_send_event",
        "session_id": "chat-123",
        "turn_id": "turn-456",
        "tool_input": arguments,
    }
    result = _inject_client(payload, client)["hookSpecificOutput"]
    updated = result["updatedInput"]
    assert updated["session_id"] == "workflow-123"
    assert updated["hiraku_client"] == {
        "client": client,
        "client_session_id": "chat-123",
        **({"client_turn_id": "turn-456"} if client == "codex" else {}),
    }
    assert arguments["hiraku_client"] == {"client": "wrong"}
    if client == "codex":
        assert result["permissionDecision"] == "allow"
    else:
        assert "permissionDecision" not in result


@pytest.mark.parametrize(
    "changes",
    [
        {"hook_event_name": "PostToolUse"},
        {"tool_name": "mcp__other__workflow_send_event"},
        {"session_id": None},
        {"session_id": ""},
        {"tool_input": []},
    ],
)
def test_hook_ignores_unusable_payloads(changes):
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "mcp__hiraku__workflow_send_event",
        "session_id": "chat-123",
        "tool_input": {},
        **changes,
    }
    assert _inject_client(payload, "codex") == {}


def test_hook_discovers_starts_without_loading_workflow_code(tmp_path):
    config = HirakuConfig.init(tmp_path, install_skill=False)
    directory = config.workflows_dir / "broken"
    directory.mkdir()
    (directory / "workflow.py").write_text("raise RuntimeError('must not execute')")
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "mcp__hiraku__broken",
        "cwd": str(tmp_path),
        "session_id": "chat",
        "tool_input": {},
    }
    assert "hookSpecificOutput" in _inject_client(payload, "claude")
    payload["tool_name"] = "mcp__hiraku__record_show"
    assert _inject_client(payload, "claude") == {}


@pytest.mark.parametrize("input", ["not json", "[]", "null"])
def test_hook_command_fails_open(input):
    result = CliRunner().invoke(main, ["session", "hook", "codex"], input=input)
    assert result.exit_code == 0
    assert "Hiraku client hook skipped" in result.stderr
    assert result.stdout == ""


def test_hook_command_reads_stdin():
    result = CliRunner().invoke(
        main,
        ["session", "hook", "claude"],
        input=json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "mcp__hiraku__workflow_send_event",
                "session_id": "chat",
                "tool_input": {"session_id": "workflow"},
            }
        ),
    )
    assert result.exit_code == 0
    assert (
        json.loads(result.stdout)["hookSpecificOutput"]["updatedInput"][
            "hiraku_client"
        ]["client_session_id"]
        == "chat"
    )
