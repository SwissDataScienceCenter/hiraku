"""Tests for `hiraku-agent update`, the draft protocol section, and the project-wide
id checks that guard every workflow that creates records.

As elsewhere in this suite, the agent is a `python3 -c` stub that prints a canned
JSON reply: what is under test is what the tool writes and refuses, not the agent's
judgement.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.errors import RecordNotFoundError
from hiraku.record import Record, load_record
from hiraku_agent import sections
from hiraku_agent.errors import AgentError
from hiraku_agent.gates import approve
from hiraku_agent.node import create_node, load_node
from hiraku_agent.workflows import (
    PROTOCOL_HEADING,
    ensure_ids_free,
    run_design,
    run_draft,
    run_revise,
    run_update,
)


def stub_agent(payload: dict) -> list[str]:
    return ["python3", "-c", "import sys; print(sys.argv[1])", json.dumps(payload)]


def _git(path: Path, *arguments: str) -> None:
    subprocess.run(["git", *arguments], cwd=path, capture_output=True, check=True)


@pytest.fixture
def config(tmp_path: Path) -> HirakuConfig:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "tracked.txt").write_text("committed")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "-qm", "initial")
    return HirakuConfig.init(tmp_path)


@pytest.fixture
def task(config: HirakuConfig) -> Record:
    return Record.create(
        "matcher",
        parent_id=None,
        type="task",
        config=config,
        goal="Pick a matcher",
        question="Which matcher ranks best?",
        summary="Benchmark.",
        metrics=[{"name": "recall@10", "direction": "maximize"}],
        markdown="Background.",
    )


DRAFT_PAYLOAD = {
    "id": "feature-matcher",
    "goal": "Choose the best feature matcher for step 3A.",
    "question": "Which matcher ranks candidate cameras best?",
    "summary": "Benchmark five matchers.",
    "body": "Step 3A currently uses LoFTR.",
    "metrics": [
        {
            "name": "Recall@10",
            "direction": "max",
            "role": "primary",
            "rationale": "standard retrieval metric",
        },
        {"name": "latency_ms", "direction": "minimize", "role": "secondary"},
        {"name": "no-direction", "role": "primary"},
    ],
    "proposed_defaults": [
        {
            "item": "Inlier threshold",
            "value": "RANSAC 1.0 px (initial)",
            "rationale": "common default",
        }
    ],
    "notes": None,
}


# ------------------------------------------------------------------------------ draft


def test_draft_records_proposed_metrics_and_defaults_in_the_body(
    config: HirakuConfig,
) -> None:
    run_draft("benchmark matchers", command=stub_agent(DRAFT_PAYLOAD), config=config)

    record = load_record("feature-matcher", config=config)
    assert [(m.name, m.direction) for m in record.metrics] == [
        ("Recall@10", "maximize"),  # "max" is normalised
        ("latency_ms", "minimize"),
    ]
    section = sections.read_section(record.markdown, PROTOCOL_HEADING)
    assert "`Recall@10` (maximize, primary) — standard retrieval metric" in section
    assert "`latency_ms` (minimize, secondary)" in section
    assert "no-direction" not in section  # dropped metric is not advertised
    assert "**Inlier threshold**: RANSAC 1.0 px (initial) — common default" in section
    assert record.markdown.startswith("Step 3A currently uses LoFTR.")


def test_draft_writes_the_agents_decision_rule_or_a_default(
    config: HirakuConfig,
) -> None:
    from hiraku_agent.workflows import DECISION_RULE_HEADING

    payload = {**DRAFT_PAYLOAD, "decision_rule": "- Target: Recall@10"}
    run_draft("benchmark", command=stub_agent(payload), config=config)
    record = load_record("feature-matcher", config=config)
    assert sections.read_section(record.markdown, DECISION_RULE_HEADING) == (
        "- Target: Recall@10"
    )

    other = {**DRAFT_PAYLOAD, "id": "other-task", "decision_rule": None}
    run_draft("benchmark", command=stub_agent(other), config=config)
    default = sections.read_section(
        load_record("other-task", config=config).markdown, DECISION_RULE_HEADING
    )
    assert "lean `inconclusive`" in default


def test_draft_refuses_an_existing_id_before_calling_the_agent(
    config: HirakuConfig, task: Record
) -> None:
    result_cmd = ["python3", "-c", "raise SystemExit('agent must not run')"]
    with pytest.raises(AgentError, match="already exist"):
        run_draft("again", task_id="matcher", command=result_cmd, config=config)


def test_draft_refuses_an_agent_chosen_id_that_already_exists(
    config: HirakuConfig, task: Record
) -> None:
    payload = {**DRAFT_PAYLOAD, "id": "matcher"}
    with pytest.raises(AgentError, match="already exist"):
        run_draft("again", command=stub_agent(payload), config=config)
    assert load_record("matcher", config=config).goal == "Pick a matcher"


def test_ensure_ids_free_rejects_repeats_within_a_batch(config: HirakuConfig) -> None:
    with pytest.raises(AgentError, match="more than once"):
        ensure_ids_free(["a", "a"], config=config)


def test_design_writes_nothing_when_any_proposed_id_is_taken(
    config: HirakuConfig, task: Record
) -> None:
    create_node("taken", task=task, kind="hypothesis", config=config)
    payload = {
        "experiments": [
            {"id": "fresh", "kind": "hypothesis", "expectation": "e"},
            {"id": "taken", "kind": "hypothesis", "expectation": "e"},
        ]
    }
    with pytest.raises(AgentError, match="'taken'"):
        run_design("matcher", command=stub_agent(payload), config=config)
    with pytest.raises(RecordNotFoundError):
        load_record("fresh", config=config)


# ------------------------------------------------------------------------------ update


def test_update_revises_a_task_nothing_refers_to(
    config: HirakuConfig, task: Record
) -> None:
    payload = {
        "goal": "Pick the best matcher for step 3A.",
        "metrics": [
            {"name": "recall@10", "direction": "maximize"},
            {"name": "latency_ms", "direction": "minimize"},
        ],
        "body": "Background.\n\n## Proposed protocol\n\n- added latency\n",
        "notes": "Added latency.",
    }
    result = run_update(
        "matcher", instruction="add latency", command=stub_agent(payload), config=config
    )

    record = load_record("matcher", config=config)
    assert record.goal == "Pick the best matcher for step 3A."
    assert record.question == "Which matcher ranks best?"  # untouched
    assert [m.name for m in record.metrics] == ["recall@10", "latency_ms"]
    assert "added latency" in record.markdown
    assert result.data["changed"] == ["goal", "metrics", "body"]


def test_update_refuses_a_task_once_a_verdict_exists_under_it(
    config: HirakuConfig, task: Record
) -> None:
    create_node(
        "exp-1", task=task, kind="hypothesis", verdict="supports", config=config
    )
    with pytest.raises(AgentError, match="Draft a new task"):
        run_update(
            "matcher",
            instruction="change goal",
            command=["python3", "-c", "raise SystemExit('must not run')"],
            config=config,
        )
    assert load_record("matcher", config=config).goal == "Pick a matcher"


RULE_BODY = "Background.\n\n## Decision rule\n\n- Target: recall@10\n"


def test_update_allows_only_the_decision_rule_while_no_verdict_exists(
    config: HirakuConfig, task: Record
) -> None:
    create_node("exp-1", task=task, kind="hypothesis", config=config)
    payload = {
        "goal": "SNEAKY new goal",
        "metrics": [{"name": "other", "direction": "minimize"}],
        "body": "Rewritten background.\n\n## Decision rule\n\n- Tolerance: 2x std\n",
        "notes": "tightened tolerance",
    }
    result = run_update(
        "matcher",
        instruction="use 2x std as tolerance",
        command=stub_agent(payload),
        config=config,
    )

    record = load_record("matcher", config=config)
    assert record.goal == "Pick a matcher"  # frozen
    assert [m.name for m in record.metrics] == ["recall@10"]  # frozen
    assert record.markdown.startswith("Background.")  # rest of the body frozen
    assert sections.read_section(record.markdown, "Decision rule") == (
        "- Tolerance: 2x std"
    )
    assert result.data["changed"] == ["decision_rule"]
    assert result.data["ignored"] == [
        "goal",
        "metrics",
        "body outside ## Decision rule",
    ]


def test_rule_only_update_needs_a_decision_rule_in_the_reply(
    config: HirakuConfig, task: Record
) -> None:
    create_node("exp-1", task=task, kind="hypothesis", config=config)
    with pytest.raises(AgentError, match="had none"):
        run_update(
            "matcher",
            instruction="x",
            command=stub_agent({"goal": "new", "body": "no section here"}),
            config=config,
        )


def test_the_prompt_tells_the_agent_which_mode_applies(
    config: HirakuConfig, task: Record
) -> None:
    assert (
        '"mode": "full"'
        in run_update("matcher", instruction="x", dry_run=True, config=config).prompt
    )
    create_node("exp-1", task=task, kind="hypothesis", config=config)
    assert (
        '"mode": "rule_only"'
        in run_update("matcher", instruction="x", dry_run=True, config=config).prompt
    )


def test_update_revises_an_experiment_without_runs_and_clears_approval(
    config: HirakuConfig, task: Record
) -> None:
    node = create_node(
        "exp-1",
        task=task,
        kind="hypothesis",
        expectation="old",
        approved_by="qz",
        approved_at="2026-10-05T10:00:00",
        config=config,
    )
    payload = {"expectation": "new", "kind": "ablation", "goal": "ignored"}
    result = run_update(
        node.id, instruction="sharpen", command=stub_agent(payload), config=config
    )

    live = load_node("exp-1", config=config)
    assert (live.expectation, live.kind) == ("new", "ablation")
    assert live.approved_by is None and live.approved_at is None
    assert result.data["approval_cleared"] is True
    assert not hasattr(live, "goal") or live.goal is None  # task fields don't leak


def test_update_refuses_an_experiment_that_has_runs(
    config: HirakuConfig, task: Record
) -> None:
    create_node("exp-1", task=task, kind="hypothesis", config=config)
    runs_csv = config.root / "runs.csv"
    runs_csv.parent.mkdir(parents=True, exist_ok=True)
    runs_csv.write_text(
        "id,record_id,commit,timestamp,entrypoint,url,metric.recall@10\n"
        "r1,exp-1,,2026-10-05T10:00:00,train,,0.5\n"
    )
    with pytest.raises(AgentError, match="already has 1 run"):
        run_update(
            "exp-1",
            instruction="edit",
            command=["python3", "-c", "raise SystemExit('must not run')"],
            config=config,
        )


def test_update_rejects_an_invalid_kind_and_writes_nothing(
    config: HirakuConfig, task: Record
) -> None:
    create_node("exp-1", task=task, kind="hypothesis", expectation="old", config=config)
    with pytest.raises(AgentError, match="one of"):
        run_update(
            "exp-1",
            instruction="edit",
            command=stub_agent({"expectation": "new", "kind": "bogus"}),
            config=config,
        )
    assert load_node("exp-1", config=config).expectation == "old"


def test_update_requires_an_instruction(config: HirakuConfig, task: Record) -> None:
    with pytest.raises(AgentError, match="instruction"):
        run_update("matcher", instruction="  ", config=config)


def test_update_dry_run_calls_nothing(config: HirakuConfig, task: Record) -> None:
    result = run_update("matcher", instruction="x", dry_run=True, config=config)
    assert "Pick a matcher" in result.prompt


# ------------------------------------------------------------------------------ --repo


def test_with_repos_adds_read_only_tools_and_add_dir(tmp_path: Path) -> None:
    from hiraku_agent.engine import with_repos

    repo = tmp_path / "other"
    repo.mkdir()
    argv = with_repos(["claude", "-p"], [str(repo)])
    assert argv == [
        "claude",
        "-p",
        "--allowedTools",
        "Read,Glob,Grep",
        "--add-dir",
        str(repo.resolve()),
    ]


def test_with_repos_respects_an_explicit_allowed_tools(tmp_path: Path) -> None:
    from hiraku_agent.engine import with_repos

    argv = with_repos(["claude", "-p", "--allowedTools", "Read"], [str(tmp_path)])
    assert argv.count("--allowedTools") == 1
    assert argv[-2:] == ["--add-dir", str(tmp_path.resolve())]


def test_with_repos_without_repos_is_a_no_op() -> None:
    from hiraku_agent.engine import with_repos

    assert with_repos(["claude", "-p"], None) == ["claude", "-p"]
    assert with_repos(None, []) is None


def test_with_repos_rejects_a_missing_directory_and_a_non_claude_agent(
    tmp_path: Path,
) -> None:
    from hiraku_agent.engine import with_repos

    with pytest.raises(AgentError, match="not a directory"):
        with_repos(["claude", "-p"], [str(tmp_path / "nope")])
    with pytest.raises(AgentError, match="needs a `claude`"):
        with_repos(["python3"], [str(tmp_path)])


def test_the_prompt_lists_the_repos_before_the_output_contract(
    config: HirakuConfig, task: Record, tmp_path: Path
) -> None:
    repo = tmp_path / "other"
    repo.mkdir()
    prompt = run_update(
        "matcher",
        instruction="x",
        dry_run=True,
        repo_paths=[str(repo)],
        command=["claude", "-p"],
        config=config,
    ).prompt
    assert "## Repositories you may read" in prompt
    assert str(repo.resolve()) in prompt
    assert prompt.index("## Repositories") < prompt.index("## Output contract")


def test_draft_passes_repo_access_to_the_agent_command(
    config: HirakuConfig, tmp_path: Path
) -> None:
    repo = tmp_path / "other"
    repo.mkdir()
    seen = tmp_path / "argv.json"
    script = tmp_path / "fake_claude"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({str(seen)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        f"print({json.dumps(json.dumps(DRAFT_PAYLOAD))})\n"
    )
    script.chmod(0o755)
    named = tmp_path / "claude"
    named.symlink_to(script)
    run_draft(
        "benchmark",
        command=[str(named), "-p"],
        repo_paths=[str(repo)],
        config=config,
    )
    argv = json.loads(seen.read_text())
    assert "--add-dir" in argv and str(repo.resolve()) in argv
    assert "Read,Glob,Grep" in argv


# -------------------------------------------------------------------- design --revise


def _three_nodes(config: HirakuConfig, task: Record) -> None:
    for node_id in ("baseline", "loftr", "roma"):
        node = create_node(
            node_id,
            task=task,
            kind="baseline" if node_id == "baseline" else "hypothesis",
            expectation=f"{node_id} old",
            config=config,
        )
        node.markdown = f"## Scope\n\n{node_id} scope\n"
        node.save()


def test_revise_edits_every_listed_node_in_one_agent_call(
    config: HirakuConfig, task: Record
) -> None:
    _three_nodes(config, task)
    payload = {
        "nodes": {
            "baseline": {
                "body": "## Scope\n\nbaseline scope\n\n## Open items\n\n- **GT** — x"
            },
            "loftr": {"expectation": "loftr new", "body": "## Scope\n\nsee baseline\n"},
        },
        "notes": None,
    }
    result = run_revise(
        "matcher",
        instruction="fill open items",
        command=stub_agent(payload),
        config=config,
    )

    assert "Open items" in load_node("baseline", config=config).markdown
    loftr = load_node("loftr", config=config)
    assert loftr.expectation == "loftr new" and "see baseline" in loftr.markdown
    roma = load_node("roma", config=config)  # not returned: untouched
    assert roma.expectation == "roma old"
    assert sorted(item.split(" ")[0] for item in result.data["changed"]) == [
        "baseline",
        "loftr",
    ]


def test_revise_never_touches_the_task_itself(
    config: HirakuConfig, task: Record
) -> None:
    _three_nodes(config, task)
    payload = {
        "nodes": {"baseline": {"expectation": "x"}},
        "goal": "HIJACKED",
        "metrics": [{"name": "other", "direction": "minimize"}],
        "notes": None,
    }
    run_revise("matcher", instruction="go", command=stub_agent(payload), config=config)

    live = load_record("matcher", config=config)
    assert live.goal == "Pick a matcher"
    assert [m.name for m in live.metrics] == ["recall@10"]


def test_revise_skips_a_node_that_has_runs_and_says_why(
    config: HirakuConfig, task: Record
) -> None:
    _three_nodes(config, task)
    (config.root / "runs.csv").write_text(
        "id,record_id,commit,timestamp,entrypoint,url,metric.recall@10\n"
        "r1,baseline,,2026-10-05T10:00:00,train,,0.5\n"
    )
    payload = {
        "nodes": {
            "baseline": {"expectation": "sneaky"},
            "loftr": {"expectation": "ok"},
        },
        "notes": None,
    }
    result = run_revise(
        "matcher", instruction="go", command=stub_agent(payload), config=config
    )

    assert load_node("baseline", config=config).expectation == "baseline old"
    assert load_node("loftr", config=config).expectation == "ok"
    assert any(item.startswith("baseline:") for item in result.data["skipped"])


def test_revise_clears_approval_only_where_the_expectation_changed(
    config: HirakuConfig, task: Record
) -> None:
    from hiraku_agent.gates import approve

    _three_nodes(config, task)
    for node_id in ("loftr", "roma"):
        approve(load_node(node_id, config=config), by="q")
    payload = {
        "nodes": {
            "loftr": {"expectation": "new"},
            "roma": {"body": "## Scope\n\nnew scope\n"},
        },
        "notes": None,
    }
    result = run_revise(
        "matcher", instruction="go", command=stub_agent(payload), config=config
    )

    assert load_node("loftr", config=config).approved_by is None
    assert load_node("roma", config=config).approved_by == "q"
    assert result.data["approvals_cleared"] == ["loftr"]


def test_revise_ignores_a_node_the_agent_invents(
    config: HirakuConfig, task: Record
) -> None:
    _three_nodes(config, task)
    payload = {"nodes": {"ghost": {"expectation": "boo"}}, "notes": None}
    result = run_revise(
        "matcher", instruction="go", command=stub_agent(payload), config=config
    )

    assert result.data["skipped"] == ["ghost: not an experiment of this task"]
    with pytest.raises(RecordNotFoundError):
        load_record("ghost", config=config)


def test_revise_needs_an_instruction_and_an_editable_node(
    config: HirakuConfig, task: Record
) -> None:
    with pytest.raises(AgentError, match="instruction"):
        run_revise("matcher", instruction="  ", config=config)
    with pytest.raises(AgentError, match="no experiments that can still be revised"):
        run_revise("matcher", instruction="go", command=stub_agent({}), config=config)


def test_revise_prompt_shows_editable_and_frozen_nodes(
    config: HirakuConfig, task: Record
) -> None:
    _three_nodes(config, task)
    (config.root / "runs.csv").write_text(
        "id,record_id,commit,timestamp,entrypoint,url,metric.recall@10\n"
        "r1,baseline,,2026-10-05T10:00:00,train,,0.5\n"
    )
    result = run_revise(
        "matcher", instruction="fill them in", dry_run=True, config=config
    )

    assert "revise the experiments already designed" in result.prompt
    state = json.loads(result.prompt.split("```json")[1].split("```")[0])
    assert sorted(state["editable"]) == ["loftr", "roma"]
    assert list(state["not_editable"]) == ["baseline"]


def test_cli_design_revise_dry_run_uses_the_revise_prompt(
    config: HirakuConfig, task: Record, monkeypatch: pytest.MonkeyPatch
) -> None:
    from click.testing import CliRunner

    from hiraku_agent.cli import main

    _three_nodes(config, task)
    monkeypatch.chdir(config.root)
    result = CliRunner().invoke(
        main, ["design", "--task", "matcher", "--revise", "-m", "fill", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert "revise the experiments already designed" in result.output


# ----------------------------------------------------------------- update --force


def _two_step_agent(update_payload: dict, revise_payload: dict) -> list[str]:
    """`update --force` calls the agent twice: the task update, then the re-sync."""
    code = (
        "import sys\n"
        "prompt = sys.argv[3]\n"
        "print(sys.argv[2] if 'revise the experiments already designed' in prompt "
        "else sys.argv[1])"
    )
    return [
        "python3",
        "-c",
        code,
        json.dumps(update_payload),
        json.dumps(revise_payload),
    ]


def test_force_updates_the_task_and_sends_experiments_back_to_proposed(
    config: HirakuConfig, task: Record
) -> None:
    _three_nodes(config, task)
    implemented = load_node("loftr", task=task, config=config)
    implemented.commit = "a" * 40
    implemented.save()
    approve(load_node("roma", task=task, config=config), by="qianqian")

    update = {"goal": "Pick a matcher for night images", "body": None}
    revise = {
        "nodes": {"roma": {"expectation": "roma wins at night", "body": None}},
        "notes": "roma adjusted",
    }
    result = run_update(
        "matcher",
        instruction="focus on night images",
        force=True,
        command=_two_step_agent(update, revise),
        config=config,
    )

    assert load_record("matcher", config=config).goal == (
        "Pick a matcher for night images"
    )
    loftr = load_node("loftr", task=task, config=config)
    assert loftr.is_proposal
    assert ("a" * 40) in sections.read_section(
        loftr.markdown, "Superseded implementation"
    )
    roma = load_node("roma", task=task, config=config)
    assert roma.expectation == "roma wins at night"
    assert roma.approved_by is None
    assert result.data["unbound"] == ["loftr"]
    assert "roma" in result.data["approvals_cleared"]
    assert result.data["synced"]
    assert "roma adjusted" in result.data["notes"]


def test_without_force_the_ignored_fields_come_with_a_hint(
    config: HirakuConfig, task: Record
) -> None:
    create_node("exp-1", task=task, kind="hypothesis", config=config)
    with pytest.raises(AgentError, match="--force"):
        run_update(
            "matcher",
            instruction="change the goal",
            command=stub_agent({"goal": "new goal", "body": "no rule here"}),
            config=config,
        )
    body = "Background.\n\n## Decision rule\n\nTarget recall@10."
    result = run_update(
        "matcher",
        instruction="change the goal and the rule",
        command=stub_agent({"goal": "new goal", "body": body}),
        config=config,
    )
    assert "goal" in result.data["ignored"]
    assert "--force" in result.data["hint"]


def test_force_is_refused_once_an_experiment_has_runs(
    config: HirakuConfig, task: Record
) -> None:
    create_node("exp-1", task=task, kind="hypothesis", config=config)
    (config.root / "runs.csv").write_text(
        "id,record_id,commit,timestamp,entrypoint,url,metric.recall@10\n"
        "r1,exp-1,,2026-10-05T10:00:00,train,,0.5\n"
    )
    with pytest.raises(AgentError, match="even with --force"):
        run_update(
            "matcher",
            instruction="x",
            force=True,
            command=["python3", "-c", "raise SystemExit('must not run')"],
            config=config,
        )


def test_force_applies_to_tasks_only(config: HirakuConfig, task: Record) -> None:
    create_node("exp-1", task=task, kind="hypothesis", config=config)
    with pytest.raises(AgentError, match="applies to a task"):
        run_update("exp-1", instruction="x", force=True, dry_run=True, config=config)


def test_force_dry_run_offers_the_full_task(config: HirakuConfig, task: Record) -> None:
    create_node("exp-1", task=task, kind="hypothesis", config=config)
    prompt = run_update(
        "matcher", instruction="x", force=True, dry_run=True, config=config
    ).prompt
    assert '"mode": "full"' in prompt
