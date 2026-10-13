"""Regression tests for the fixes to bugs found in review: the scheduler's handling of
unapproved and unanalysable nodes, analyze/triage bookkeeping, all-or-nothing node
creation, implement's commit check and timeout, and the loop's repeat guard."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

from hiraku.agent import Action, Program
from hiraku.config import HirakuConfig
from hiraku.record import Record, list_records
from hiraku.run import Run
from hiraku_agent import loop as loop_module
from hiraku_agent import sections
from hiraku_agent import state as state_module
from hiraku_agent.defaults import AgentDefaults, save_defaults
from hiraku_agent.engine import AgentRun
from hiraku_agent.errors import AgentError, AgentTimeout
from hiraku_agent.gates import approve
from hiraku_agent.loop import run_loop
from hiraku_agent.node import Node, create_node, load_node
from hiraku_agent.schedule import suggest_next
from hiraku_agent.workflows import (
    run_analyze,
    run_design,
    run_implement,
    run_triage,
)


def _git(path: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=path, text=True, capture_output=True, check=True
    ).stdout.strip()


@pytest.fixture
def config(tmp_path: Path) -> HirakuConfig:
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init", "-q")
    _git(project, "config", "user.email", "test@example.invalid")
    _git(project, "config", "user.name", "Test")
    (project / "tracked.txt").write_text("committed")
    _git(project, "add", "tracked.txt")
    _git(project, "commit", "-qm", "initial")
    return HirakuConfig.init(project)


@pytest.fixture
def task(config: HirakuConfig) -> Record:
    return Record.create(
        "vo-backend",
        parent_id=None,
        type="task",
        config=config,
        goal="Improve validation accuracy",
        entrypoints={"train": {"command": ["python", "train.py"]}},
        metrics=[{"name": "val_accuracy", "direction": "maximize"}],
    )


def stub_agent(payload: dict) -> list[str]:
    return ["python3", "-c", "import sys; print(sys.argv[1])", json.dumps(payload)]


def _run(run_id: str, record_id: str = "baseline") -> Run:
    return Run(
        id=run_id,
        metrics={"val_accuracy": 0.8},
        params={},
        record_id=record_id,
        commit="a" * 40,
        timestamp="2026-10-01T00:00:00Z",
        entrypoint="train",
        url="",
        raw={},
    )


def _implemented(config: HirakuConfig, task: Record, node_id: str = "baseline") -> Node:
    node = create_node(
        node_id, task=task, kind="baseline", expectation="x", config=config
    )
    node.commit = "a" * 40
    node.save()
    return load_node(node_id, task=task, config=config)


def _with_runs(monkeypatch: pytest.MonkeyPatch, runs: dict[str, list[Run]]) -> None:
    monkeypatch.setattr(Node, "runs", lambda self, **_: runs.get(self.id, []))


ANALYZE_NULL = {
    "verdict": None,
    "findings": "No baseline to compare against.",
    "conclusions": "Cannot judge yet.",
    "next_steps": None,
    "promotable_candidate": None,
    "missing_evidence": "Runs of the reference matcher.",
}


# ----------------------------------------------------------------- bug 3: scheduler


def test_implemented_but_unapproved_node_is_sent_to_approve(
    config: HirakuConfig, task: Record
) -> None:
    _implemented(config, task)  # guided mode, never approved
    suggestion = suggest_next("vo-backend", config=config)
    assert suggestion["action"] == "approve"
    assert suggestion["node"] == "baseline"
    assert "matches the expectation" in suggestion["why"]


# ------------------------------------------------- bug 2: analyze null and the loop


def test_a_null_analysis_is_not_repeated_until_new_runs_arrive(
    config: HirakuConfig, task: Record, monkeypatch: pytest.MonkeyPatch
) -> None:
    _implemented(config, task)
    runs = {"baseline": [_run("r1")]}
    _with_runs(monkeypatch, runs)
    assert suggest_next("vo-backend", config=config)["action"] == "analyze"

    run_analyze("baseline", command=stub_agent(ANALYZE_NULL), config=config)

    node = load_node("baseline", task=task, config=config)
    assert node.verdict is None
    assert node.analyzed_runs == ["r1"]
    suggestion = suggest_next("vo-backend", config=config)
    assert suggestion["action"] == "needs_evidence"
    assert "reference matcher" in suggestion["why"]

    runs["baseline"].append(_run("r2"))
    assert suggest_next("vo-backend", config=config)["action"] == "analyze"


def _program(config: HirakuConfig, tmp_path: Path, payload: dict) -> None:
    script = tmp_path / "stub_agent.py"
    script.write_text(f"print({json.dumps(json.dumps(payload))})\n")
    action = Action(
        id="auto-analyze",
        entity="vo-backend",
        markdown="",
        workflow="analyze",
        agent_cmd=f"python3 {script}",
    )
    action.save(config.actions_dir / "vo-backend" / "auto-analyze" / "action.md")
    program = Program(id="loop", markdown="", actions=["auto-analyze"])
    program.save(config.programs_dir / "loop" / "program.md")


def test_loop_stops_after_one_null_analysis(
    config: HirakuConfig, task: Record, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _implemented(config, task)
    _with_runs(monkeypatch, {"baseline": [_run("r1")]})
    _program(config, tmp_path, ANALYZE_NULL)

    result = run_loop("loop", "vo-backend", config=config)

    assert [step.action for step in result.steps] == ["analyze"]
    assert "needs_evidence" in result.stopped


def test_loop_stops_when_the_same_step_would_repeat_unchanged(
    config: HirakuConfig, task: Record, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _implemented(config, task)
    _with_runs(monkeypatch, {"baseline": [_run("r1")]})
    _program(config, tmp_path, ANALYZE_NULL)
    # A scheduler that keeps asking for the same call, whatever happened.
    monkeypatch.setattr(
        loop_module,
        "suggest_next",
        lambda *a, **k: {"action": "analyze", "node": "baseline", "why": "test"},
    )

    result = run_loop("loop", "vo-backend", config=config)

    # The first call changes `analyzed_runs`; the second changes nothing, so a third
    # is refused.
    assert len(result.steps) == 2
    assert "again" in result.stopped


def test_loop_passes_project_defaults_to_each_step(
    config: HirakuConfig, task: Record, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _implemented(config, task)
    _with_runs(monkeypatch, {"baseline": [_run("r1")]})
    _program(config, tmp_path, ANALYZE_NULL)
    code = tmp_path / "code"
    code.mkdir()
    save_defaults(AgentDefaults(repos=[str(code)]), config)
    seen: dict = {}

    def fake_analyze(node_id, *, repo_paths=None, **kwargs):
        seen["repo_paths"] = repo_paths
        return AgentRun(workflow="analyze", prompt="")

    monkeypatch.setitem(loop_module.LLM_WORKFLOWS, "analyze", fake_analyze)
    run_loop("loop", "vo-backend", config=config)
    assert seen["repo_paths"] == [str(code)]


# ---------------------------------------------------- bug 8d: analyze while running


def test_analyze_refuses_a_running_node_unless_allowed(
    config: HirakuConfig, task: Record, monkeypatch: pytest.MonkeyPatch
) -> None:
    _implemented(config, task)
    _with_runs(monkeypatch, {"baseline": [_run("r1")]})
    monkeypatch.setattr(
        state_module, "running_nodes", lambda runs, cfg: {"baseline": {}}
    )
    with pytest.raises(Exception, match="still running"):
        run_analyze("baseline", command=stub_agent(ANALYZE_NULL), config=config)

    payload = {**ANALYZE_NULL, "findings": "Early numbers."}
    run_analyze(
        "baseline", command=stub_agent(payload), allow_running=True, config=config
    )
    findings = sections.read_section(
        load_node("baseline", task=task, config=config).markdown, "Findings"
    )
    assert findings.startswith("_Partial results")
    assert "Early numbers." in findings


# ----------------------------------------------------------------- bug 7: class A


TRIAGE_A = {
    "blocked": None,
    "failure_class": "A",
    "class_evidence": "r1 crashed with a shape mismatch.",
    "verdict": None,
    "findings": "r1 crashed.",
    "conclusions": "Execution defect.",
    "investigation_test": {},
    "proceed": False,
    "blocking_condition": None,
    "children": [],
}


def test_class_a_clears_the_verdict_and_asks_for_a_fix(
    config: HirakuConfig, task: Record, monkeypatch: pytest.MonkeyPatch
) -> None:
    node = _implemented(config, task)
    node.verdict = "refutes"
    node.save()
    runs = {"baseline": [_run("r1")]}
    _with_runs(monkeypatch, runs)

    run_triage("baseline", command=stub_agent(TRIAGE_A), config=config)

    node = load_node("baseline", task=task, config=config)
    assert node.verdict is None
    assert node.triaged is False
    assert node.execution_defect is True
    assert node.defect_runs == ["r1"]
    assert suggest_next("vo-backend", config=config)["action"] == "fix"

    runs["baseline"].append(_run("r2"))
    assert suggest_next("vo-backend", config=config)["action"] == "analyze"
    dry = run_analyze("baseline", dry_run=True, config=config)
    assert "defect_runs" in dry.prompt


# ------------------------------------------------------ bug 5: atomic node creation


def _design(nodes: list[dict]) -> dict:
    return {"experiments": nodes, "notes": None}


def _item(node_id: str, parent: str | None) -> dict:
    return {
        "id": node_id,
        "kind": "hypothesis",
        "origin": "agent-diagnostic",
        "parent": parent,
        "expectation": "val_accuracy rises.",
    }


def test_design_creates_a_child_listed_before_its_parent(
    config: HirakuConfig, task: Record
) -> None:
    payload = _design([_item("child", "base"), _item("base", None)])
    result = run_design("vo-backend", command=stub_agent(payload), config=config)
    assert [path.stem for path in result.written] == ["child", "base"]
    assert load_node("child", task=task, config=config).parent_id == "base"


def test_design_refuses_a_parent_in_another_task_and_writes_nothing(
    config: HirakuConfig, task: Record
) -> None:
    other = Record.create("other", parent_id=None, type="task", config=config, goal="g")
    create_node("elsewhere", task=other, kind="baseline", config=config)
    before = {record.id for record in list_records(config=config)}
    payload = _design([_item("fine", None), _item("stray", "elsewhere")])
    with pytest.raises(AgentError, match="outside task"):
        run_design("vo-backend", command=stub_agent(payload), config=config)
    assert {record.id for record in list_records(config=config)} == before


def test_design_refuses_a_cycle(config: HirakuConfig, task: Record) -> None:
    payload = _design([_item("a", "b"), _item("b", "a")])
    with pytest.raises(AgentError, match="cycle"):
        run_design("vo-backend", command=stub_agent(payload), config=config)
    assert not [r for r in list_records(config=config) if r.id in {"a", "b"}]


def test_triage_does_not_mark_the_node_when_its_children_fail(
    config: HirakuConfig, task: Record, monkeypatch: pytest.MonkeyPatch
) -> None:
    node = _implemented(config, task)
    node.verdict = "refutes"
    node.save()
    _with_runs(monkeypatch, {"baseline": [_run("r1")]})
    payload = {
        **TRIAGE_A,
        "failure_class": "B",
        "verdict": "refutes",
        "proceed": True,
        # An id that is already taken: the whole batch is refused.
        "children": [
            {"id": "fresh-child", "kind": "diagnostic"},
            {"id": "vo-backend", "kind": "diagnostic"},
        ],
    }
    with pytest.raises(AgentError, match="already exist"):
        run_triage("baseline", command=stub_agent(payload), config=config)
    assert load_node("baseline", task=task, config=config).triaged is False
    assert "fresh-child" not in {r.id for r in list_records(config=config)}


# -------------------------------------------------- bug 6 and 8b: implement


def _approved_proposal(config: HirakuConfig, task: Record) -> None:
    node = create_node(
        "baseline", task=task, kind="baseline", expectation="x", config=config
    )
    approve(node, by="qianqian")


def test_implement_refuses_a_commit_that_already_existed(
    config: HirakuConfig, task: Record
) -> None:
    _approved_proposal(config, task)
    payload = {"blocked": None, "commit": "HEAD", "notes": "did it"}
    with pytest.raises(AgentError, match="already existed"):
        run_implement("baseline", command=stub_agent(payload), config=config)
    assert load_node("baseline", task=task, config=config).is_proposal


def test_implement_timeout_stops_the_whole_group_and_keeps_the_edits(
    config: HirakuConfig, task: Record, tmp_path: Path
) -> None:
    _approved_proposal(config, task)
    pid_file = tmp_path / "grandchild.pid"
    script = (
        "import subprocess, time\n"
        "open('partial.py', 'w').write('# half done\\n')\n"
        "child = subprocess.Popen(['sleep', '60'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    with pytest.raises(AgentTimeout) as caught:
        run_implement(
            "baseline", command=["python3", "-c", script], timeout=2, config=config
        )
    message = str(caught.value)
    assert "partial.py" in message
    assert "not bound" in message
    # Kept, not discarded — in the agent's own worktree, never in the user's tree.
    kept = Path(re.search(r"kept in (\S+):", message).group(1))
    assert (kept / "partial.py").exists()
    assert not (config.root / "partial.py").exists()
    assert load_node("baseline", task=task, config=config).is_proposal

    grandchild = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("the agent's own child process survived the timeout")


# ------------------------------------------- implement worktrees live outside .git


def test_implement_worktree_is_outside_git_and_out_of_git_status(
    config: HirakuConfig, task: Record, tmp_path: Path
) -> None:
    _approved_proposal(config, task)
    seen = tmp_path / "cwd.txt"
    script = f"import os, json; open({str(seen)!r}, 'w').write(os.getcwd()); " + (
        "print(json.dumps({'blocked': 'just looking', 'commit': None}))"
    )
    for _ in range(2):  # twice: the exclude entry is added once
        run_implement("baseline", command=["python3", "-c", script], config=config)
    cwd = Path(seen.read_text())
    assert ".hiraku-worktrees" in cwd.parts
    assert ".git" not in cwd.parts
    exclude = (config.root / ".git" / "info" / "exclude").read_text()
    assert exclude.count("/.hiraku-worktrees/") == 1
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=config.root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert ".hiraku-worktrees" not in status


def test_a_worktree_dir_under_git_is_refused(
    config: HirakuConfig, task: Record, monkeypatch: pytest.MonkeyPatch
) -> None:
    _approved_proposal(config, task)
    monkeypatch.setenv("HIRAKU_AGENT_WORKTREE_DIR", str(config.root / ".git" / "wt"))
    with pytest.raises(AgentError, match="inside a .git directory"):
        run_implement(
            "baseline",
            command=["python3", "-c", "raise SystemExit('must not run')"],
            config=config,
        )


# ------------------------------------------- implement: time budget and smoke tests


def _budget(prompt: str) -> int:
    state = json.loads(prompt.split("```json")[1].split("```")[0])
    return state["time_budget_minutes"]


def test_implement_gets_its_own_longer_timeout_and_is_told_it(
    config: HirakuConfig, task: Record
) -> None:
    _approved_proposal(config, task)
    dry = run_implement("baseline", dry_run=True, config=config)
    assert _budget(dry.prompt) == 60
    assert "Smoke-test as you build" in dry.prompt
    assert "HIRAKU_RECORD_ID" in dry.prompt
    assert "Commit once, at the end" in dry.prompt
    assert ".hiraku-progress.md" in dry.prompt

    save_defaults(AgentDefaults(implement_timeout=1800), config)
    assert _budget(run_implement("baseline", dry_run=True, config=config).prompt) == 30
    explicit = run_implement("baseline", dry_run=True, timeout=600, config=config)
    assert _budget(explicit.prompt) == 10

    blocked = {"blocked": "just checking", "commit": None}
    result = run_implement("baseline", command=stub_agent(blocked), config=config)
    meta = json.loads((result.log / "meta.json").read_text())
    assert meta["timeout"] == 1800
