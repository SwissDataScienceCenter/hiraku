"""Tests for the project agent defaults (`hiraku/agent.yaml`), the `plan` command, and
the shared "decide, label, proceed" policy in the prompts."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.config import HirakuConfig
from hiraku.record import Record, load_record
from hiraku_agent import sections
from hiraku_agent.cli import main
from hiraku_agent.defaults import (
    AgentDefaults,
    load_defaults,
    merge_defaults,
    save_defaults,
)
from hiraku_agent.engine import build_prompt
from hiraku_agent.errors import AgentError
from hiraku_agent.node import load_node
from hiraku_agent.workflows import run_plan


def _git(path: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=path, capture_output=True, check=True)


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
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "code"
    path.mkdir()
    (path / "CLAUDE.md").write_text("GT file: data/gt.csv")
    return path


@pytest.fixture(autouse=True)
def _no_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HIRAKU_AGENT_NO_DEFAULTS", raising=False)


# ------------------------------------------------------------------- defaults


def test_no_file_means_no_defaults(config: HirakuConfig) -> None:
    assert load_defaults(config).is_empty()
    kwargs = {"repo_paths": [], "context_paths": []}
    merged, notes = merge_defaults(kwargs, config)
    assert merged == kwargs and notes == []


def test_save_and_load_round_trip(config: HirakuConfig) -> None:
    save_defaults(
        AgentDefaults(repos=["~/code"], context_paths=["notes.md"], context_id="ctx"),
        config,
    )
    loaded = load_defaults(config)
    assert (loaded.repos, loaded.context_paths, loaded.context_id) == (
        ["~/code"],
        ["notes.md"],
        "ctx",
    )


def test_merge_adds_repos_and_files_to_what_the_call_names(
    config: HirakuConfig, repo: Path
) -> None:
    notes_file = config.root / "notes.md"
    notes_file.write_text("context")
    save_defaults(
        AgentDefaults(
            repos=[str(repo), "/does/not/exist"],
            context_paths=["notes.md"],
            context_id="ctx",
        ),
        config,
    )
    merged, notices = merge_defaults(
        {"repo_paths": ["/explicit"], "context_paths": [], "context_id": None}, config
    )
    assert merged["repo_paths"] == ["/explicit", str(repo)]
    assert merged["context_paths"] == [str(notes_file)]
    assert merged["context_id"] == "ctx"
    assert any(
        "/does/not/exist" in notice and "skipped" in notice for notice in notices
    )


def test_an_explicit_context_id_wins_and_unlisted_keys_are_untouched(
    config: HirakuConfig, repo: Path
) -> None:
    save_defaults(AgentDefaults(repos=[str(repo)], context_id="default-ctx"), config)
    merged, _ = merge_defaults({"context_id": "mine"}, config)
    # `implement` takes no repo_paths: a default repo must not be added to it.
    assert merged == {"context_id": "mine"}


def test_opt_out_env_ignores_the_file(
    config: HirakuConfig, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_defaults(AgentDefaults(repos=[str(repo)]), config)
    monkeypatch.setenv("HIRAKU_AGENT_NO_DEFAULTS", "1")
    merged, notices = merge_defaults({"repo_paths": []}, config)
    assert merged == {"repo_paths": []} and notices == []


def test_bad_file_is_a_clear_error(config: HirakuConfig) -> None:
    (config.hiraku_dir / "agent.yaml").write_text("repo: {a: b}\n")
    with pytest.raises(AgentError, match="repo"):
        load_defaults(config)


def test_cli_defaults_set_show_and_feed_a_dry_run(
    config: HirakuConfig, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Record.create(
        "matcher",
        parent_id=None,
        type="task",
        config=config,
        goal="Pick a matcher",
        question="Which matcher ranks best?",
    )
    monkeypatch.chdir(config.root)
    runner = CliRunner()
    shown = runner.invoke(main, ["defaults"])
    assert shown.exit_code == 0 and "no project defaults" in shown.output

    written = runner.invoke(main, ["defaults", "--repo", str(repo)])
    assert written.exit_code == 0, written.output
    assert f"repo: {repo}" in written.output

    dry = runner.invoke(main, ["design", "--task", "matcher", "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert str(repo) in dry.output  # the repositories block is in the prompt

    cleared = runner.invoke(main, ["defaults", "--clear"])
    assert cleared.exit_code == 0
    assert load_defaults(config).is_empty()


# -------------------------------------------------------------------------- plan

DRAFT = {
    "id": "feature-matcher",
    "goal": "Pick the best matcher",
    "question": "Which matcher ranks best?",
    "summary": "Benchmark matchers.",
    "body": "Background.",
    "metrics": [
        {"name": "Recall@10", "direction": "maximize", "role": "primary"},
    ],
    "proposed_defaults": [
        {
            "item": "Resizing",
            "value": "identical inputs for all methods",
            "status": "default",
            "rationale": "comparability",
        }
    ],
    "decision_rule": "Target Recall@10.",
    "candidate_methods": None,
    "notes": "draft note",
}
DESIGN = {
    "experiments": [
        {
            "id": "baseline",
            "kind": "baseline",
            "origin": "agent-diagnostic",
            "parent": None,
            "expectation": "Recall@10 is the reference value.",
            "scope": "Reference.",
            "expectation_body": "Reference.",
            "rationale": "Nothing else is measurable without it.",
            "open_items": [
                {
                    "item": "keypoint budget",
                    "value": "2048",
                    "status": "default",
                    "source": "common setting",
                },
                {
                    "item": "GPU",
                    "value": None,
                    "status": "unset",
                    "source": "no config names one",
                },
            ],
        }
    ],
    "notes": "design note",
}


def _plan_stub() -> list[str]:
    code = (
        "import sys, json\n"
        "print(sys.argv[1] if 'draft a new task' in sys.argv[3] else sys.argv[2])"
    )
    return ["python3", "-c", code, json.dumps(DRAFT), json.dumps(DESIGN)]


def test_plan_drafts_the_task_then_designs_its_nodes(config: HirakuConfig) -> None:
    result = run_plan("benchmark matchers", command=_plan_stub(), config=config)

    task = load_record("feature-matcher", config=config)
    assert "**Resizing**" in sections.read_section(task.markdown, "Proposed protocol")
    assert "default, initial value" in task.markdown
    node = load_node("baseline", task=task, config=config)
    items = sections.read_section(node.markdown, "Open items")
    assert "**keypoint budget** — 2048 (default, to be calibrated" in items
    assert "**GPU** — _not determined_ (unset, needs the researcher" in items
    assert len(result.written) == 2
    assert result.data["task"] == "feature-matcher"
    assert "task: draft note" in result.data["notes"]
    assert "experiments: design note" in result.data["notes"]


def test_plan_dry_run_and_no_apply_stop_after_the_draft(config: HirakuConfig) -> None:
    dry = run_plan("benchmark matchers", dry_run=True, config=config)
    assert "draft a new task" in dry.prompt

    applied = run_plan(
        "benchmark matchers", command=_plan_stub(), apply=False, config=config
    )
    assert applied.written == []
    with pytest.raises(Exception, match="feature-matcher"):
        load_record("feature-matcher", config=config)


def test_plan_names_the_task_when_design_fails(config: HirakuConfig) -> None:
    bad_design = {"nodes": []}
    code = (
        "import sys, json\n"
        "print(sys.argv[1] if 'draft a new task' in sys.argv[3] else sys.argv[2])"
    )
    command = ["python3", "-c", code, json.dumps(DRAFT), json.dumps(bad_design)]
    with pytest.raises(AgentError, match="was written.*design --task feature-matcher"):
        run_plan("benchmark matchers", command=command, config=config)
    assert load_record("feature-matcher", config=config).id == "feature-matcher"


# ---------------------------------------------------------------------------- prompts


@pytest.mark.parametrize(
    "workflow", ["draft", "update", "design", "revise", "discover", "analyze"]
)
def test_every_prompt_carries_the_unknown_value_policy(workflow: str) -> None:
    prompt = build_prompt(workflow, {})
    assert "decide, label, proceed" in prompt
    assert "{{assumptions}}" not in prompt


def test_rules_no_longer_forbid_looking_in_the_project() -> None:
    rules = build_prompt("design", {})
    assert "never guess" not in rules.lower()
    assert "Look before" not in rules  # old wording, replaced by the shared policy
    assert "CLAUDE.md" in rules


def test_draft_keeps_thresholds_and_paths_out_of_the_task() -> None:
    draft = build_prompt("draft", {})
    assert "principle" in draft
    assert "No thresholds, hyperparameters, paths or hardware here" in draft
