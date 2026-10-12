"""Tests for the numbers `analyze` computes in code (`hiraku_agent.compare`), the
`## Metric comparison` / `## Missing evidence` sections of a node's analysis, and
task-level `analyze`.

As elsewhere, the agent is a `python3 -c` stub; runs are stub `Run` objects patched onto
`Node.runs`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hiraku.config import HirakuConfig
from hiraku.errors import HirakuError
from hiraku.record import Record, load_record
from hiraku.run import Run
from hiraku_agent import compare, sections
from hiraku_agent.node import Node, create_node, load_node
from hiraku_agent.workflows import run_analyze


def stub_agent(payload: dict) -> list[str]:
    return ["python3", "-c", "import sys; print(sys.argv[1])", json.dumps(payload)]


def _run(run_id: str, node: str, **metrics: float) -> Run:
    return Run(id=run_id, record_id=node, metrics=metrics)


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
        question="Which matcher is best?",
        metrics=[
            {"name": "ba", "direction": "maximize"},
            {"name": "sec", "direction": "minimize"},
        ],
        markdown="## Decision rule\n\n- Target: ba\n",
    )


@pytest.fixture
def runs(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Run]]:
    table: dict[str, list[Run]] = {}
    monkeypatch.setattr(Node, "runs", lambda self, **_: table.get(self.id, []))
    return table


# ------------------------------------------------------------------------- compare


def test_metric_stat_mean_sample_std_and_skips_non_numbers() -> None:
    stat = compare.metric_stat(
        [_run("a", "n", ba=0.8), _run("b", "n", ba=0.9), _run("c", "n", other=1.0)],
        "ba",
    )
    assert stat.n == 2
    assert stat.mean == pytest.approx(0.85)
    assert stat.std == pytest.approx(0.0707106, rel=1e-4)
    assert compare.metric_stat([_run("a", "n", ba=0.8)], "ba").std is None
    assert compare.metric_stat([], "ba").mean is None


def test_improvement_is_signed_by_direction() -> None:
    assert compare.improvement(0.1, "maximize") == 0.1
    assert compare.improvement(0.1, "minimize") == -0.1


def test_comparison_against_baseline_scales_by_baseline_spread(
    config: HirakuConfig, task: Record
) -> None:
    base = create_node("base", task=task, kind="baseline", config=config)
    cand = create_node("cand", task=task, kind="hypothesis", config=config)
    runs = {
        "base": [
            _run("1", "base", ba=0.80, sec=1.0),
            _run("2", "base", ba=0.82, sec=1.0),
        ],
        "cand": [_run("3", "cand", ba=0.90, sec=2.0)],
    }
    result = compare.compare_to_baselines(task, [base, cand], runs, focus="cand")
    rows = {row.metric: row for row in result.baselines["base"]}
    assert rows["ba"].delta == pytest.approx(0.09)
    assert rows["ba"].improvement == pytest.approx(0.09)
    assert rows["ba"].improvement_in_baseline_std == pytest.approx(
        0.09 / 0.0141421, rel=1e-3
    )
    assert rows["sec"].improvement == pytest.approx(-1.0)  # slower is worse
    assert "vs `base`" in compare.comparison_markdown(result)


def test_comparison_is_empty_without_a_baseline_that_has_runs(
    config: HirakuConfig, task: Record
) -> None:
    base = create_node("base", task=task, kind="baseline", config=config)
    cand = create_node("cand", task=task, kind="hypothesis", config=config)
    result = compare.compare_to_baselines(
        task, [base, cand], {"cand": [_run("1", "cand", ba=0.9)]}, focus="cand"
    )
    assert not result


def test_leaderboard_ranks_marks_pending_and_not_dominated(
    config: HirakuConfig, task: Record
) -> None:
    nodes = [
        create_node(name, task=task, kind="hypothesis", config=config)
        for name in ("fast", "best", "worse", "todo")
    ]
    runs = {
        "fast": [_run("1", "fast", ba=0.80, sec=1.0)],
        "best": [_run("2", "best", ba=0.90, sec=3.0)],
        "worse": [_run("3", "worse", ba=0.70, sec=4.0)],
    }
    board = compare.build_leaderboard(task, nodes, runs)
    assert board.ranking["ba"] == ["best", "fast", "worse"]
    assert board.ranking["sec"] == ["fast", "best", "worse"]  # minimize: fastest first
    assert set(board.pareto) == {"fast", "best"}  # "worse" is beaten everywhere
    assert board.pending == ("todo",)
    text = compare.leaderboard_markdown(board)
    assert "Partial" in text and "`todo`" in text
    assert "`best` *" in text and "`worse` *" not in text


# --------------------------------------------------------------- node-level analyze


def test_node_analyze_writes_comparison_and_missing_evidence_sections(
    config: HirakuConfig, task: Record, runs: dict[str, list[Run]]
) -> None:
    create_node("base", task=task, kind="baseline", config=config)
    create_node("cand", task=task, kind="hypothesis", expectation="x", config=config)
    runs["base"] = [_run("1", "base", ba=0.80, sec=1.0)]
    runs["cand"] = [_run("2", "cand", ba=0.85, sec=2.0)]
    payload = {
        "verdict": "inconclusive",
        "findings": "Within tolerance.",
        "conclusions": "Not separable.",
        "next_steps": "Add seeds.",
        "missing_evidence": "Two more baseline seeds.",
    }
    run_analyze("cand", command=stub_agent(payload), config=config)

    node = load_node("cand", config=config)
    assert node.verdict == "inconclusive"
    table = sections.read_section(node.markdown, "Metric comparison")
    assert "vs `base`" in table and "0.85" in table
    assert sections.read_section(node.markdown, "Missing evidence") == (
        "Two more baseline seeds."
    )
    # Computed numbers come first, then the agent's reading of them.
    assert node.markdown.index("Metric comparison") < node.markdown.index("Findings")

    # A re-run that now reports nothing missing clears the stale section, not stacks it.
    run_analyze(
        "cand",
        command=stub_agent({**payload, "missing_evidence": None}),
        config=config,
    )
    node = load_node("cand", config=config)
    assert sections.read_section(node.markdown, "Missing evidence").startswith("None")
    assert node.markdown.count("## Missing evidence") == 1


def test_node_analyze_prompt_carries_the_computed_table(
    config: HirakuConfig, task: Record, runs: dict[str, list[Run]]
) -> None:
    create_node("base", task=task, kind="baseline", config=config)
    create_node("cand", task=task, kind="hypothesis", config=config)
    runs["base"] = [_run("1", "base", ba=0.80)]
    runs["cand"] = [_run("2", "cand", ba=0.85)]
    prompt = run_analyze("cand", dry_run=True, config=config).prompt
    assert '"metric_comparison"' in prompt


# --------------------------------------------------------------- task-level analyze


def test_task_analyze_writes_result_sections_into_the_task_record(
    config: HirakuConfig, task: Record, runs: dict[str, list[Run]]
) -> None:
    create_node("base", task=task, kind="baseline", config=config)
    create_node("cand", task=task, kind="hypothesis", config=config)
    create_node("todo", task=task, kind="hypothesis", config=config)
    runs["base"] = [_run("1", "base", ba=0.80, sec=1.0)]
    runs["cand"] = [_run("2", "cand", ba=0.90, sec=2.0)]
    payload = {
        "findings": "cand leads on ba.",
        "conclusions": "Recommend cand for review.",
        "next_steps": "Run todo.",
        "missing_evidence": "todo has no runs.",
        "verdict": "supports",  # a task has no verdict: must be ignored
    }
    result = run_analyze("matcher", command=stub_agent(payload), config=config)

    record = load_record("matcher", config=config)
    assert result.written == [record.path]
    assert "verdict" not in record.model_dump()
    assert sections.read_section(record.markdown, "Decision rule") == "- Target: ba"
    assert sections.read_section(record.markdown, "Conclusions") == (
        "Recommend cand for review."
    )
    table = sections.read_section(record.markdown, "Metric comparison")
    assert "Partial" in table and "`todo`" in table and "`cand`" in table
    assert sections.read_section(record.markdown, "Missing evidence") == (
        "todo has no runs."
    )


def test_task_analyze_requires_some_runs(config: HirakuConfig, task: Record) -> None:
    create_node("base", task=task, kind="baseline", config=config)
    with pytest.raises(HirakuError, match="no evidence to analyse"):
        run_analyze("matcher", config=config)


def test_task_analyze_prompt_has_the_metric_table_and_no_raw_runs(
    config: HirakuConfig, task: Record, runs: dict[str, list[Run]]
) -> None:
    create_node("base", task=task, kind="baseline", config=config)
    runs["base"] = [_run("1", "base", ba=0.80)]
    prompt = run_analyze("matcher", dry_run=True, config=config).prompt
    assert '"metric_table"' in prompt
    assert "task-level counterpart" in prompt


def test_task_analyze_works_on_findings_alone_without_any_runs_or_metric_table(
    config: HirakuConfig, task: Record
) -> None:
    """Investigation/diagnostic nodes produce findings, not runs: a task made of them
    can still be analysed jointly, and no empty metric table is written."""
    diag = create_node("why-fails", task=task, kind="diagnostic", config=config)
    diag.markdown = sections.set_section(diag.markdown, "Findings", "Fails on night.")
    diag.save()
    create_node("other", task=task, kind="diagnostic", config=config)
    payload = {
        "findings": "why-fails: night images fail.",
        "insights": "Possible shared cause (untested): low light.",
        "conclusions": "Likely illumination.",
        "next_steps": "Test with relit images.",
        "missing_evidence": None,
    }
    prompt = run_analyze("matcher", dry_run=True, config=config).prompt
    assert '"metric_table"' not in prompt
    assert "Fails on night." in prompt

    run_analyze("matcher", command=stub_agent(payload), config=config)
    record = load_record("matcher", config=config)
    assert sections.read_section(record.markdown, "Cross-node insights").startswith(
        "Possible shared cause"
    )
    assert "Metric comparison" not in record.markdown
    # Reading order: findings, then what they show together, then the conclusion.
    order = [
        record.markdown.index(f"## {h}")
        for h in ("Findings", "Cross-node insights", "Conclusions")
    ]
    assert order == sorted(order)


def test_task_analyze_prompt_asks_for_joint_insight_and_labels_inference(
    config: HirakuConfig, task: Record, runs: dict[str, list[Run]]
) -> None:
    create_node("base", task=task, kind="baseline", config=config)
    runs["base"] = [_run("1", "base", ba=0.80)]
    prompt = run_analyze("matcher", dry_run=True, config=config).prompt
    assert "together" in prompt and "inference" in prompt
    assert '"insights"' in prompt


# ------------------------------------------------------------ subset (--nodes) analysis


def test_subset_analysis_goes_in_its_own_section_and_leaves_the_rest_alone(
    config: HirakuConfig, task: Record, runs: dict[str, list[Run]]
) -> None:
    for name in ("a", "b", "c"):
        create_node(name, task=task, kind="hypothesis", config=config)
        runs[name] = [_run(name, name, ba=0.8, sec=1.0)]
    whole = {"findings": "whole", "conclusions": "all three", "next_steps": "n"}
    run_analyze("matcher", command=stub_agent(whole), config=config)

    subset = {
        "findings": "a vs b",
        "insights": "they agree",
        "conclusions": "pair only",
    }
    prompt = run_analyze("matcher", only=["a", "b"], dry_run=True, config=config).prompt
    assert '"scope"' in prompt and '"analysed_only"' in prompt
    assert '"id": "c"' not in prompt  # left-out nodes are not shown to the agent

    run_analyze("matcher", only=["a", "b"], command=stub_agent(subset), config=config)
    record = load_record("matcher", config=config)
    # The whole-task conclusion is untouched...
    assert sections.read_section(record.markdown, "Conclusions") == "all three"
    # ...and the subset has a section of its own that says what it did not cover.
    body = sections.read_section(record.markdown, "Subset analysis: a, b")
    assert "Analysed only: `a`, `b`" in body and "`c`" in body
    assert "whole-task conclusion" in body
    assert "### Conclusions\n\npair only" in body

    # Re-running the same subset replaces its section; a different subset adds another.
    run_analyze(
        "matcher",
        only=["a", "b"],
        command=stub_agent({"findings": "again"}),
        config=config,
    )
    run_analyze(
        "matcher",
        only=["b", "c"],
        command=stub_agent({"findings": "bc"}),
        config=config,
    )
    record = load_record("matcher", config=config)
    assert record.markdown.count("## Subset analysis: a, b\n") == 1
    assert "again" in sections.read_section(record.markdown, "Subset analysis: a, b")
    assert "## Subset analysis: b, c" in record.markdown


def test_subset_analysis_rejects_unknown_ids_and_node_targets(
    config: HirakuConfig, task: Record, runs: dict[str, list[Run]]
) -> None:
    create_node("a", task=task, kind="hypothesis", config=config)
    runs["a"] = [_run("1", "a", ba=0.8)]
    with pytest.raises(HirakuError, match="Not nodes of this task: typo"):
        run_analyze("matcher", only=["a", "typo"], dry_run=True, config=config)
    with pytest.raises(HirakuError, match="applies to a task"):
        run_analyze("a", only=["a"], dry_run=True, config=config)


def test_selecting_every_node_is_a_whole_task_analysis(
    config: HirakuConfig, task: Record, runs: dict[str, list[Run]]
) -> None:
    create_node("a", task=task, kind="hypothesis", config=config)
    runs["a"] = [_run("1", "a", ba=0.8)]
    run_analyze(
        "matcher", only=["a"], command=stub_agent({"findings": "f"}), config=config
    )
    record = load_record("matcher", config=config)
    assert "Subset analysis" not in record.markdown
    assert sections.read_section(record.markdown, "Findings") == "f"
