"""Tests for `hiraku_agent`'s LLM-driven workflows (`engine.py`, `sections.py`,
`workflows.py`).

No real LLM is called anywhere here. Instead, `command=` is pointed at a tiny
`python3 -c` script that prints a canned JSON reply — the same trick the old
`hiraku_auto` test suite used, and the same idea as `test_hiraku_agent.py`'s
`test_a_real_csv_backend_row_moves_the_scheduler`: real subprocess plumbing
(`engine.call_agent`, prompt delivery, logging), fake judgement. What's under
test is "does the tool write what the agent said," not "does the agent say
something good" — the latter is `prompts/*.md`'s job and is not something a
unit test can grade.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.data import Task
from hiraku_agent import sections
from hiraku_agent.engine import (
    Bundle,
    attach_context,
    build_prompt,
    extract_json,
    gather_context,
    prompt_delivery,
)
from hiraku_agent.errors import AgentError
from hiraku_agent.node import create_node, load_node
from hiraku_agent.workflows import run_analyze, run_design, run_discover, run_triage


@pytest.fixture
def config(tmp_path: Path) -> HirakuConfig:
    return HirakuConfig.init(tmp_path)


@pytest.fixture
def task(config: HirakuConfig) -> Task:
    return Task.create(
        "vo-backend",
        config=config,
        goal="Improve validation accuracy",
        entrypoints={"train": {"command": ["python", "train.py"]}},
        metrics=[{"name": "val_accuracy", "direction": "maximize"}],
    )


def stub_agent(payload: dict) -> list[str]:
    """A fake agent: prints `payload` as JSON, ignoring whatever prompt it was given.

    `sys.argv[1]` is the JSON text (see this module's docstring); the prompt
    `engine.prompt_delivery` appends after it lands in `sys.argv[2]` and is never read.
    """
    return ["python3", "-c", "import sys; print(sys.argv[1])", json.dumps(payload)]


# -------------------------------------------------------------------------- sections.py


def test_set_section_creates_a_new_heading() -> None:
    out = sections.set_section("", "Findings", "Run r1 hit 0.9.")
    assert out.strip() == "## Findings\n\nRun r1 hit 0.9."


def test_set_section_replaces_only_its_own_heading() -> None:
    base = "## Findings\n\nold\n\n## Conclusions\n\nkeep me\n\n"
    out = sections.set_section(base, "Findings", "new")
    assert sections.read_section(out, "Findings") == "new"
    assert sections.read_section(out, "Conclusions") == "keep me"


def test_append_section_adds_to_existing_body() -> None:
    base = sections.set_section("", "Decisions and next steps", "first")
    out = sections.append_section(base, "Decisions and next steps", "second")
    assert out.count("## Decisions and next steps") == 1
    assert "first" in out
    assert "second" in out


def test_read_missing_section_is_empty_string() -> None:
    assert sections.read_section("## Findings\n\nx\n", "Conclusions") == ""


# ---------------------------------------------------------------------------- engine.py


def test_extract_json_from_a_fenced_block() -> None:
    reply = 'Sure, here it is:\n```json\n{"verdict": "supports"}\n```\nDone.'
    assert extract_json(reply) == {"verdict": "supports"}


def test_extract_json_from_bare_braces_with_surrounding_prose() -> None:
    reply = 'Notes before.\n{"a": 1, "b": [1, 2]}\nNotes after.'
    assert extract_json(reply) == {"a": 1, "b": [1, 2]}


def test_extract_json_unwraps_claude_output_format_json() -> None:
    inner = json.dumps({"verdict": "refutes"})
    wrapped = json.dumps({"result": inner, "other": "meta"})
    assert extract_json(wrapped) == {"verdict": "refutes"}


def test_extract_json_raises_with_no_object_in_reply() -> None:
    with pytest.raises(AgentError):
        extract_json("I could not find a JSON object here.")


def test_prompt_delivery_appends_for_a_non_claude_command() -> None:
    argv, stdin = prompt_delivery(["python3", "-c", "pass"], "the prompt")
    assert argv == ["python3", "-c", "pass", "the prompt"]
    assert stdin is None


def test_prompt_delivery_uses_stdin_for_claude() -> None:
    argv, stdin = prompt_delivery(["claude", "-p"], "the prompt")
    assert argv == ["claude", "-p"]
    assert stdin == "the prompt"


def test_prompt_delivery_substitutes_the_placeholder() -> None:
    argv, stdin = prompt_delivery(["agent", "--in", "{prompt}"], "hello")
    assert argv == ["agent", "--in", "hello"]
    assert stdin is None


def test_build_prompt_fills_in_state_and_instruction() -> None:
    prompt = build_prompt(
        "design", {"task": {"id": "vo-backend"}}, instruction="try dropout"
    )
    assert '"id": "vo-backend"' in prompt
    assert "try dropout" in prompt
    assert "{{state}}" not in prompt
    assert "{{instruction}}" not in prompt
    assert "{{rules}}" not in prompt


def test_build_prompt_rejects_an_unknown_workflow() -> None:
    with pytest.raises(AgentError):
        build_prompt("bogus-workflow", {})


def test_gather_context_reads_named_files_only(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("dataset lives at /data/vo", encoding="utf-8")
    (tmp_path / "unread.md").write_text("should not appear", encoding="utf-8")
    bundle = gather_context(tmp_path, ["notes.md"])
    assert "notes.md" in bundle.files
    assert "unread.md" not in bundle.files
    assert bool(bundle) is True


def test_gather_context_skips_a_missing_file_quietly(tmp_path: Path) -> None:
    bundle = gather_context(tmp_path, ["does-not-exist.md"])
    assert not bundle.files
    assert bool(bundle) is False


def test_attach_context_only_adds_the_key_when_non_empty() -> None:
    empty = attach_context({"a": 1}, Bundle())
    assert "project_context" not in empty
    full = attach_context({"a": 1}, Bundle(files={"x.md": "text"}))
    assert full["project_context"] == [{"path": "x.md", "excerpt": "text"}]


# --------------------------------------------------------------------------- run_design


def test_run_design_writes_proposed_nodes_from_the_agent_reply(
    config: HirakuConfig, task: Task
) -> None:
    payload = {
        "experiments": [
            {
                "id": "baseline",
                "kind": "baseline",
                "origin": "agent-diagnostic",
                "parent": None,
                "expectation": "A ResNet-18 baseline reaches 0.80 val_accuracy.",
                "scope": "The reference run everything else compares to.",
                "expectation_body": "Predicted: 0.80 +/- 0.02.",
                "rationale": "Nothing else can be judged without a reference.",
            }
        ],
        "notes": None,
    }
    result = run_design("vo-backend", command=stub_agent(payload), config=config)

    assert result.data == payload
    assert len(result.written) == 1
    node = load_node("baseline", task=task, config=config)
    assert node.kind == "baseline"
    assert node.origin == "agent-diagnostic"
    assert node.expectation.startswith("A ResNet-18")
    assert node.is_proposal
    assert sections.read_section(node.markdown, "Scope").startswith("The reference")
    assert "Nothing else" in sections.read_section(node.markdown, "Rationale")


def test_run_design_dry_run_calls_nothing_and_writes_nothing(
    config: HirakuConfig, task: Task
) -> None:
    result = run_design("vo-backend", dry_run=True, config=config)
    assert result.raw == ""
    assert result.written == []
    assert "design experiments for a task" in result.prompt


def test_run_design_no_apply_calls_the_agent_but_writes_nothing(
    config: HirakuConfig, task: Task
) -> None:
    payload = {"experiments": [{"id": "baseline", "expectation": "x"}], "notes": None}
    result = run_design(
        "vo-backend", command=stub_agent(payload), apply=False, config=config
    )
    assert result.data == payload
    assert result.written == []
    with pytest.raises(Exception):  # noqa: B017 - RecordNotFoundError, not our concern
        load_node("baseline", task=task, config=config)


def test_run_design_raises_if_the_reply_has_no_experiments_key(
    config: HirakuConfig, task: Task
) -> None:
    with pytest.raises(AgentError):
        run_design("vo-backend", command=stub_agent({"notes": "oops"}), config=config)


# ------------------------------------------------------------------------- run_discover


def test_run_discover_writes_accepted_and_declined_as_nodes(
    config: HirakuConfig, task: Task
) -> None:
    payload = {
        "proposals": [
            {
                "id": "focal-loss",
                "kind": "hypothesis",
                "origin": "agent-literature",
                "proposed_parent": None,
                "title": "Focal loss for class imbalance",
                "expectation": "Focal loss raises val_accuracy by >= 0.02.",
                "provenance": "Lin et al. 2017, widely reproduced.",
                "applicability": "Our label distribution is similarly skewed.",
                "prediction": "val_accuracy up, precision on rare class up.",
                "investigation_test": "1: named cause - yes. ...",
                "cost": "One extra run, no new dependency.",
                "counterargument": "Might just need better sampling instead.",
            }
        ],
        "declined": [
            {
                "id": "gan-augmentation",
                "kind": "hypothesis",
                "origin": "agent-literature",
                "title": "GAN-based augmentation",
                "expectation": "Synthetic data improves generalization.",
                "failing_condition": "5 -- proportionate",
                "reason": "Needs a new dependency and days of GPU time for unclear "
                "gain.",
                "belongs_under": None,
            }
        ],
        "injected_instructions_found": None,
        "notes": None,
    }
    result = run_discover("vo-backend", command=stub_agent(payload), config=config)

    assert len(result.written) == 2
    accepted = load_node("focal-loss", task=task, config=config)
    assert accepted.origin == "agent-literature"
    assert accepted.is_proposal
    assert not accepted.abandoned
    assert "Lin et al" in sections.read_section(accepted.markdown, "Provenance")

    declined = load_node("gan-augmentation", task=task, config=config)
    assert declined.abandoned is True
    assert declined.commit is None
    why = sections.read_section(declined.markdown, "Why declined")
    assert "5 -- proportionate" in why
    assert "new dependency" in why


def test_run_discover_skips_an_id_that_already_exists(
    config: HirakuConfig, task: Task
) -> None:
    create_node("focal-loss", task=task, kind="hypothesis", config=config)
    payload = {
        "proposals": [{"id": "focal-loss", "expectation": "duplicate"}],
        "declined": [],
        "notes": None,
    }
    result = run_discover("vo-backend", command=stub_agent(payload), config=config)
    assert result.written == []
    assert result.data["skipped_existing"] == ["focal-loss"]


# --------------------------------------------------------------------------- run_triage


def test_run_triage_sets_verdict_and_creates_a_child_on_class_b(
    config: HirakuConfig, task: Task
) -> None:
    node = create_node(
        "baseline",
        task=task,
        kind="baseline",
        expectation="Reaches 0.80 val_accuracy.",
        config=config,
    )
    node.commit = "a" * 40
    node.save()

    payload = {
        "blocked": None,
        "failure_class": "B",
        "class_evidence": "run r1 hit 0.62, well under 0.80.",
        "verdict": "refutes",
        "findings": "r1: val_accuracy 0.62.",
        "conclusions": "Class B: the baseline architecture underperforms as stated.",
        "investigation_test": {
            "named_cause": {"pass": True, "note": "label smoothing may help"},
            "discriminating": {"pass": True, "note": "..."},
            "novel": {"pass": True, "note": "not tried yet"},
            "in_scope": {"pass": True, "note": ""},
            "proportionate": {"pass": True, "note": "one cheap run"},
            "named_metric": {"pass": True, "note": "val_accuracy, maximize"},
        },
        "proceed": True,
        "blocking_condition": None,
        "children": [
            {
                "id": "label-smoothing",
                "kind": "diagnostic",
                "expectation": "Label smoothing recovers >= 0.75 val_accuracy.",
                "scope": "Responds directly to baseline's shortfall.",
                "expectation_body": "Predicted: 0.75-0.80.",
            }
        ],
    }
    result = run_triage(
        "baseline", task_id="vo-backend", command=stub_agent(payload), config=config
    )

    reloaded = load_node("baseline", task=task, config=config)
    assert reloaded.verdict == "refutes"
    assert "0.62" in sections.read_section(reloaded.markdown, "Findings")

    child = load_node("label-smoothing", task=task, config=config)
    assert child.parent == "baseline"
    assert child.kind == "diagnostic"
    assert child.is_proposal
    assert len(result.written) == 2


def test_run_triage_class_a_sets_no_verdict_and_no_children(
    config: HirakuConfig, task: Task
) -> None:
    create_node(
        "baseline", task=task, kind="baseline", expectation="x", config=config
    ).save()
    node = load_node("baseline", task=task, config=config)
    node.commit = "a" * 40
    node.save()

    payload = {
        "blocked": None,
        "failure_class": "A",
        "class_evidence": "run crashed with a shape mismatch.",
        "verdict": None,
        "findings": "r1 crashed before producing metrics.",
        "conclusions": "Execution defect, not a result.",
        "investigation_test": {},
        "proceed": False,
        "blocking_condition": "execution failed",
        "children": [],
    }
    result = run_triage(
        "baseline", task_id="vo-backend", command=stub_agent(payload), config=config
    )

    reloaded = load_node("baseline", task=task, config=config)
    assert reloaded.verdict is None
    assert len(result.written) == 1  # only the node itself, no children


def test_run_triage_blocked_reply_writes_nothing(
    config: HirakuConfig, task: Task
) -> None:
    create_node("baseline", task=task, config=config)
    payload = {"blocked": "no expectation recorded"}
    result = run_triage(
        "baseline", task_id="vo-backend", command=stub_agent(payload), config=config
    )
    assert result.written == []
    assert result.data["blocked"] == "no expectation recorded"


# -------------------------------------------------------------------------- run_analyze


def test_run_analyze_sets_verdict_and_writes_findings(
    config: HirakuConfig, task: Task, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hiraku.data import Run
    from hiraku_agent.node import Node

    node = create_node(
        "baseline", task=task, kind="baseline", expectation="x", config=config
    )
    node.commit = "a" * 40
    node.save()

    # `run_analyze` requires runs to exist before it will even build a prompt, and
    # `workflows.task_state` serializes each run's fields into the prompt (unlike the
    # scheduler, which only checks truthiness) — so the stub has to be a real `Run`, not
    # a bare `object()` the way `test_hiraku_agent.py`'s scheduling tests get away with.
    # The tracking backend itself has its own real, non-mocked coverage in
    # `test_a_real_csv_backend_row_moves_the_scheduler`; this just needs *a* run to
    # exist.
    stub_run = Run(
        id="r1",
        metrics={"val_accuracy": 0.83},
        params={},
        task_id="vo-backend",
        experiment_id="baseline",
        commit="a" * 40,
        timestamp="2026-09-18T00:00:00Z",
        entrypoint="train",
        url="",
        raw={},
    )
    monkeypatch.setattr(Node, "runs", lambda self, **_: [stub_run])

    payload = {
        "verdict": "supports",
        "findings": "r1: val_accuracy 0.83, above the 0.80 target.",
        "conclusions": "Expectation met; single seed so far.",
        "next_steps": "Run 3 more seeds before treating this as a new baseline.",
        "promotable_candidate": "r1",
        "missing_evidence": None,
    }
    result = run_analyze(
        "baseline", task_id="vo-backend", command=stub_agent(payload), config=config
    )

    reloaded = load_node("baseline", task=task, config=config)
    assert reloaded.verdict == "supports"
    assert "0.83" in sections.read_section(reloaded.markdown, "Findings")
    next_steps = sections.read_section(reloaded.markdown, "Decisions and next steps")
    assert "3 more seeds" in next_steps
    assert "Candidate run: `r1`" in next_steps
    assert len(result.written) == 1


def test_run_analyze_raises_without_runs(config: HirakuConfig, task: Task) -> None:
    from hiraku.errors import HirakuError

    node = create_node("baseline", task=task, config=config)
    node.commit = "a" * 40
    node.save()

    with pytest.raises(HirakuError):
        run_analyze("baseline", task_id="vo-backend", config=config)
