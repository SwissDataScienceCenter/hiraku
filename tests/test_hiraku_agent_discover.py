"""Tests for `discover` as investigation: working on any record, writing cited findings
and unverified inferences into that record's own file, and `--findings-only`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.errors import HirakuError
from hiraku.record import Record, load_record
from hiraku_agent import sections
from hiraku_agent.engine import UNVERIFIED_FILES_NOTE, files_readable
from hiraku_agent.node import create_node, load_node
from hiraku_agent.workflows import run_discover


def stub_agent(payload: dict) -> list[str]:
    return ["python3", "-c", "import sys; print(sys.argv[1])", json.dumps(payload)]


@pytest.fixture
def config(tmp_path: Path) -> HirakuConfig:
    return HirakuConfig.init(tmp_path)


@pytest.fixture
def task(config: HirakuConfig) -> Record:
    return Record.create(
        "matcher",
        parent_id=None,
        type="task",
        config=config,
        goal="Pick a matcher",
        metrics=[{"name": "ba", "direction": "maximize"}],
    )


INVESTIGATION = {
    "findings": "feature_match.py:40 uses a fixed 0.2 ratio threshold.",
    "inferences": "Probably too strict for night images (untested).",
    "open_questions": "Is the threshold tuned per dataset?",
}


def test_findings_only_on_a_node_writes_to_that_nodes_file_and_proposes_nothing(
    config: HirakuConfig, task: Record, tmp_path: Path
) -> None:
    create_node("why-fails", task=task, kind="diagnostic", config=config)
    notes = tmp_path / "notes.md"
    notes.write_text("some context")
    payload = {
        "proposals": [{"id": "should-be-ignored", "expectation": "x"}],
        "declined": [{"id": "also-ignored", "expectation": "y"}],
        "investigation": INVESTIGATION,
    }
    result = run_discover(
        "matcher",
        on="why-fails",
        findings_only=True,
        instruction="why does it fail at night",
        context_paths=[str(notes)],
        command=stub_agent(payload),
        config=config,
    )

    node = load_node("why-fails", config=config)
    entry = sections.read_section(node.markdown, "Investigation")
    assert "— why does it fail at night" in entry
    assert "**Established**" in entry and "feature_match.py:40" in entry
    assert "**Inferred, not verified**" in entry and "untested" in entry
    assert UNVERIFIED_FILES_NOTE not in entry  # a --context file was supplied
    assert result.written == [node.path]
    assert result.data["investigation_written_to"] == "why-fails"
    with pytest.raises(HirakuError):
        load_record("should-be-ignored", config=config)


def test_investigation_entries_accumulate_rather_than_overwrite(
    config: HirakuConfig, task: Record, tmp_path: Path
) -> None:
    ctx = tmp_path / "c.md"
    ctx.write_text("x")
    for text in ("first pass", "second pass"):
        run_discover(
            "matcher",
            findings_only=True,
            instruction=text,
            context_paths=[str(ctx)],
            command=stub_agent({"investigation": {"findings": text}}),
            config=config,
        )
    body = sections.read_section(
        load_record("matcher", config=config).markdown, "Investigation"
    )
    assert "first pass" in body and "second pass" in body
    assert body.count("### ") == 2


def test_files_readable_reflects_what_the_agent_was_actually_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hiraku_agent.engine import AGENT_CAPABILITIES_ENV_VAR, Bundle

    assert files_readable(["claude", "-p"], None) is False
    assert files_readable(["claude", "-p", "--allowedTools", "Read,Glob,Grep"], None)
    assert files_readable(["claude", "-p", "--dangerously-skip-permissions"], None)
    assert files_readable(["claude", "-p"], Bundle(files={"notes.md": "x"}))
    # Another agent's permissions cannot be read off its command line: none are
    # assumed until declared, so its findings stay marked unverified.
    monkeypatch.delenv(AGENT_CAPABILITIES_ENV_VAR, raising=False)
    assert files_readable(["some-other-agent"], None) is False
    monkeypatch.setenv(AGENT_CAPABILITIES_ENV_VAR, "web, files")
    assert files_readable(["some-other-agent"], None)


def test_unverified_note_is_written_when_the_agent_could_not_read_files(
    config: HirakuConfig, task: Record, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hiraku_agent.flows.discover as discover_module

    monkeypatch.setattr(discover_module, "files_readable", lambda *_a, **_k: False)
    run_discover(
        "matcher",
        findings_only=True,
        command=stub_agent({"investigation": INVESTIGATION}),
        config=config,
    )
    body = sections.read_section(
        load_record("matcher", config=config).markdown, "Investigation"
    )
    assert UNVERIFIED_FILES_NOTE in body


def test_proposals_from_a_node_default_to_being_its_children(
    config: HirakuConfig, task: Record
) -> None:
    create_node("parent-node", task=task, kind="diagnostic", config=config)
    payload = {
        "proposals": [
            {"id": "child-a", "expectation": "e"},
            {"id": "elsewhere", "expectation": "e", "proposed_parent": "matcher"},
        ],
        "declined": [],
    }
    run_discover(
        "matcher", on="parent-node", command=stub_agent(payload), config=config
    )
    assert load_record("child-a", config=config).parent_id == "parent-node"
    assert load_record("elsewhere", config=config).parent_id == "matcher"


def test_discover_rejects_a_node_from_another_task(
    config: HirakuConfig, task: Record
) -> None:
    other = Record.create("other-task", parent_id=None, type="task", config=config)
    create_node("foreign", task=other, kind="hypothesis", config=config)
    with pytest.raises(HirakuError, match="does not belong"):
        run_discover("matcher", on="foreign", dry_run=True, config=config)


def test_the_prompt_carries_focus_and_mode(config: HirakuConfig, task: Record) -> None:
    create_node("n", task=task, kind="diagnostic", config=config)
    prompt = run_discover(
        "matcher", on="n", findings_only=True, dry_run=True, config=config
    ).prompt
    assert '"mode": "findings_only"' in prompt
    assert '"id": "n"' in prompt and '"kind": "experiment"' in prompt
    assert "Investigating what already exists" in prompt
