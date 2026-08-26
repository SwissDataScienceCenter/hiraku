"""Reading the project's own files, and drafting a goal record from a question.

Two things are being protected here. First, that the digest sent to the agent stays bounded and
stays inside the project — it is prompt content, and prompt content leaves the machine. Second,
that a value the agent could not ground survives as an `**[UNSET: ...]**` marker all the way from
its reply into `check`, rather than being quietly smoothed into prose.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_agent import fake_agent

from hiraku_auto import agent as agent_mod
from hiraku_auto import context as context_mod
from hiraku_auto import yamlio
from hiraku_auto.errors import HirakuError
from hiraku_auto.records import load_experiment, load_goal, read_record, unset_markers
from hiraku_auto.tree import build, check

# --------------------------------------------------------------------------- collection


def test_default_sources_pick_up_the_memory_file_and_docs(bare_project):
    (bare_project.root / "CLAUDE.md").write_text("Reprojection error is in pixels.", "utf-8")
    (bare_project.root / "docs").mkdir()
    (bare_project.root / "docs" / "metrics.md").write_text("Median over eval frames.", "utf-8")

    bundle = context_mod.collect(bare_project)
    names = {f.rel for f in bundle.files}
    assert "CLAUDE.md" in names
    assert "docs/metrics.md" in names
    # research/config.md is a configured source; the goal records are not, because the state
    # already carries them in full.
    assert "research/config.md" in names


def test_records_are_never_swept_in_as_context(project):
    (project.root / "CLAUDE.md").write_text("x", "utf-8")
    bundle = context_mod.collect(project, extra=["research/**/*.md"])
    assert not [f for f in bundle.files if f.rel.startswith("research/goals/")]
    assert not [f for f in bundle.files if f.rel.startswith("research/experiments/")]


def test_environments_and_caches_are_skipped(bare_project):
    (bare_project.root / ".venv" / "lib").mkdir(parents=True)
    (bare_project.root / ".venv" / "lib" / "README.md").write_text("noise", "utf-8")
    bundle = context_mod.collect(bare_project, extra=["**/*.md"])
    assert not [f for f in bundle.files if ".venv" in f.rel]


def test_a_large_file_is_truncated_not_dropped(bare_project):
    (bare_project.root / "CLAUDE.md").write_text("x" * 50_000, "utf-8")
    bundle = context_mod.collect(bare_project)
    entry = next(f for f in bundle.files if f.rel == "CLAUDE.md")
    assert entry.truncated
    assert len(entry.text) <= context_mod.DEFAULT_LIMITS["max_bytes_per_file"]
    assert "truncated" in entry.to_dict()


def test_the_total_cap_holds_across_files(bare_project):
    (bare_project.root / "docs").mkdir()
    for index in range(30):
        (bare_project.root / "docs" / f"n{index}.md").write_text("y" * 30_000, "utf-8")
    bundle = context_mod.collect(bare_project)
    assert bundle.total_chars <= context_mod.DEFAULT_LIMITS["max_total_bytes"]
    assert bundle.skipped


def test_a_sibling_repository_can_be_named_as_a_source(bare_project, tmp_path):
    """The research project and the code it studies are usually separate repositories."""
    sibling = tmp_path / "mapillary_alignment"
    sibling.mkdir()
    (sibling / "CLAUDE.md").write_text("reprojection error is median pixel L2.", "utf-8")

    bundle = context_mod.collect(bare_project, extra=[str(sibling / "CLAUDE.md")])
    entry = next(f for f in bundle.files if f.rel.endswith("CLAUDE.md"))
    assert entry.rel == "../mapillary_alignment/CLAUDE.md"
    assert entry.outside
    assert "another repository" in entry.to_dict()["from"]
    assert "from outside this project" in bundle.summary()
    assert "median pixel L2" in entry.text


def test_a_relative_path_may_climb_out_of_the_project(bare_project, tmp_path):
    sibling = tmp_path / "code-repo"
    (sibling / "docs").mkdir(parents=True)
    (sibling / "docs" / "metrics.md").write_text("the metric is defined here", "utf-8")

    bundle = context_mod.collect(bare_project, extra=["../code-repo/docs/**/*.md"])
    assert [f.rel for f in bundle.files if f.outside] == ["../code-repo/docs/metrics.md"]


def test_a_whole_sibling_directory_expands_to_its_markdown(bare_project, tmp_path):
    sibling = tmp_path / "code-repo"
    (sibling / "docs").mkdir(parents=True)
    (sibling / "docs" / "a.md").write_text("a", "utf-8")
    (sibling / "big.bin").write_bytes(b"\x00" * 10)

    bundle = context_mod.collect(bare_project, extra=[str(sibling)])
    assert [f.rel for f in bundle.files if f.outside] == ["../code-repo/docs/a.md"]


def test_credential_locations_are_refused_wherever_they_are(bare_project, tmp_path):
    keys = tmp_path / ".ssh"
    keys.mkdir()
    (keys / "notes.md").write_text("private", "utf-8")
    with pytest.raises(HirakuError, match="credentials or private keys"):
        context_mod.collect(bare_project, extra=[str(keys / "notes.md")])

    env = tmp_path / "repo"
    env.mkdir()
    (env / ".env").write_text("TOKEN=abc", "utf-8")
    with pytest.raises(HirakuError, match="credentials or private keys"):
        context_mod.collect(bare_project, extra=[str(env / ".env")])


def test_a_home_or_filesystem_root_is_refused(bare_project):
    with pytest.raises(HirakuError, match="whole home or filesystem directory"):
        context_mod.collect(bare_project, extra=[str(Path.home())])


def test_collection_can_be_turned_off_two_ways(bare_project):
    (bare_project.root / "CLAUDE.md").write_text("x", "utf-8")
    assert not context_mod.collect(bare_project, enabled=False)

    config = read_record(bare_project.config_md)
    config.front["context"] = {"sources": []}
    config.path.write_text(config.render(), encoding="utf-8")
    assert not context_mod.collect(bare_project)


def test_the_config_template_survives_a_yaml_free_parser():
    """`context.sources` is a block list; the fallback parser has to read one."""
    text = (
        "project: p\n"
        "bounds:\n  max_runs_per_goal: 40\n"
        "context:\n  sources:\n    - CLAUDE.md\n    - docs/**/*.md\n  max_files: 40\n"
        "promotion:\n  reproduction_seeds: 1\n"
    )
    parsed = yamlio._fallback_load(text)
    assert parsed["context"]["sources"] == ["CLAUDE.md", "docs/**/*.md"]
    assert parsed["context"]["max_files"] == 40
    assert parsed["bounds"]["max_runs_per_goal"] == 40
    assert parsed["promotion"]["reproduction_seeds"] == 1


# --------------------------------------------------------------------------- in the prompt


def test_context_reaches_the_design_prompt_and_can_be_suppressed(project, tmp_path):
    (project.root / "CLAUDE.md").write_text(
        "ate_rmse_m is metres, over the whole trajectory.", "utf-8"
    )

    with_context = agent_mod.run_design(project, "vo-backend", dry_run=True)
    assert "over the whole trajectory" in with_context.prompt
    assert "project_context" in with_context.prompt

    without = agent_mod.run_design(project, "vo-backend", dry_run=True, use_context=False)
    assert "over the whole trajectory" not in without.prompt


def test_triage_does_not_carry_context_unless_asked(project):
    (project.root / "CLAUDE.md").write_text("a distinctive sentence", "utf-8")
    default = agent_mod.run_triage(project, "candidate", dry_run=True)
    assert "a distinctive sentence" not in default.prompt
    asked = agent_mod.run_triage(project, "candidate", dry_run=True, use_context=True)
    assert "a distinctive sentence" in asked.prompt


# --------------------------------------------------------------------------- drafting


DRAFT = {
    "title": "VGGT pose reconstruction",
    "primary_metric": "reprojection_error_px_median",
    "direction": "minimize",
    "target": 3.5,
    "gate_metric": "kept_frame_rate",
    "question": "Does DINOv3 close the Mapillary-Natix domain gap?",
    "why_it_matters": "The alignment pipeline depends on it.",
    "success_criterion": "Median reprojection error below 3.5 px on three seeds.",
    "evaluation_protocol": {
        "dataset": "**[UNSET: dataset version and checksum — no dataset path in the files]**",
        "splits": "Frames 0-2699 evaluate; the rest are held out.",
        "metrics": "Median pixel L2 over evaluation frames at native resolution.",
        "seeds": "Three: 0, 1, 2; reduced by median.",
        "held_fixed": "Intrinsics, resolution, frame list.",
    },
    "out_of_scope": "Retraining VGGT.",
    "open_threads": "Whether rig-camera VO shares the cause.",
    "first_experiments": [
        {
            "id": "dinov2-retrieval",
            "kind": "baseline",
            "expectation": "DINOv2 retrieval recovers 60% of the pairs, fixing the reference.",
            "scope": "the reference point",
            "rationale": "nothing can be measured against nothing",
        }
    ],
    "unset": [
        {
            "field": "evaluation_protocol.dataset",
            "why": "no dataset path appears in the project files",
            "how_to_fill": "the Mapillary slice id and its sha256",
        }
    ],
    "grounding": [{"claim": "the pipeline uses VGGT", "source": "CLAUDE.md"}],
    "notes": None,
}


def test_draft_writes_a_complete_goal_and_keeps_the_unset_marker(bare_project, tmp_path):
    (bare_project.root / "CLAUDE.md").write_text("We reconstruct with VGGT.", "utf-8")
    result = agent_mod.run_draft(
        bare_project,
        "vggt-pose",
        "reconstruction between Mapillary and Natix is poor; would DINOv3 help?",
        command=fake_agent(tmp_path, json.dumps(DRAFT)),
    )
    assert result.context, "the draft prompt should carry the project files"
    assert "We reconstruct with VGGT." in result.prompt

    record = load_goal(bare_project, "vggt-pose")
    assert record.front["primary_metric"] == "reprojection_error_px_median"
    assert record.front["target"] == 3.5
    assert record.front["gate_metric"] == "kept_frame_rate"

    protocol = record.section("Evaluation protocol")
    for heading in ("Dataset", "Splits", "Metrics", "Seeds", "Held fixed"):
        assert f"### {heading}" in protocol
    assert "0-2699" in protocol
    assert "the pipeline uses VGGT" in record.section("History")

    markers = unset_markers(record)
    assert len(markers) == 1
    assert markers[0][0].startswith("Evaluation")
    assert "checksum" in markers[0][1]


def test_a_missing_protocol_part_becomes_a_marker_rather_than_a_gap(bare_project, tmp_path):
    partial = {**DRAFT, "evaluation_protocol": {"dataset": "d", "metrics": "m"}}
    agent_mod.run_draft(
        bare_project, "half-drafted", "q", command=fake_agent(tmp_path, json.dumps(partial))
    )
    needed = {needed for _, needed in unset_markers(load_goal(bare_project, "half-drafted"))}
    assert any("splits" in n for n in needed)
    assert any("seeds" in n for n in needed)
    assert any("held fixed" in n for n in needed)


def test_check_reports_unset_values(bare_project, tmp_path):
    agent_mod.run_draft(
        bare_project, "vggt-pose", "q", command=fake_agent(tmp_path, json.dumps(DRAFT))
    )
    issues = check(build(bare_project))
    assert any("vggt-pose: unset in" in issue and "checksum" in issue for issue in issues)


def test_draft_writes_no_experiments_unless_asked(bare_project, tmp_path):
    reply = json.dumps(DRAFT)
    agent_mod.run_draft(bare_project, "vggt-pose", "q", command=fake_agent(tmp_path, reply))
    assert not (bare_project.experiments_dir / "dinov2-retrieval").exists()

    agent_mod.run_draft(
        bare_project,
        "vggt-pose-two",
        "q",
        command=fake_agent(tmp_path, reply),
        with_experiments=True,
    )
    node = load_experiment(bare_project, "dinov2-retrieval")
    assert node.front["status"] == "proposed"
    assert node.front["goal"] == "vggt-pose-two"
    assert "nothing can be measured" in node.section("Decisions")


def test_drafting_over_an_existing_goal_needs_revise(bare_project, tmp_path):
    reply = fake_agent(tmp_path, json.dumps(DRAFT))
    agent_mod.run_draft(bare_project, "vggt-pose", "q", command=reply)
    with pytest.raises(HirakuError, match="--revise"):
        agent_mod.run_draft(bare_project, "vggt-pose", "q", command=reply)

    agent_mod.run_draft(bare_project, "vggt-pose", "q", command=reply, revise=True)
    history = load_goal(bare_project, "vggt-pose").section("History")
    assert "goal opened" in history and "redrafted" in history


def test_a_draft_without_a_question_is_refused(bare_project):
    with pytest.raises(HirakuError, match="question is required"):
        agent_mod.run_draft(bare_project, "vggt-pose", "   ", dry_run=True)


def test_a_reply_without_a_metric_is_refused(bare_project, tmp_path):
    from hiraku_auto.errors import AgentError

    with pytest.raises(AgentError, match="primary_metric"):
        agent_mod.run_draft(
            bare_project, "vggt-pose", "q", command=fake_agent(tmp_path, '{"title": "x"}')
        )


# --------------------------------------------------------------------------- removal


def test_goal_rm_deletes_an_empty_goal_and_its_proposals(bare_project, tmp_path):
    from hiraku_auto.scaffold import create_goal, create_proposal, remove_goal

    create_goal(bare_project, "mistake", primary_metric="x")
    create_proposal(bare_project, "an-idea", goal="mistake", origin="agent-literature")

    removal = remove_goal(bare_project, "mistake")
    names = {p.name for p in removal.paths}
    assert names == {"mistake.md", "an-idea.md"}
    assert not (bare_project.goals_dir / "mistake.md").exists()
    assert check(build(bare_project)) == []


def test_goal_rm_refuses_while_experiments_hang_off_it(project):
    from hiraku_auto.scaffold import remove_goal

    with pytest.raises(HirakuError, match="still has 2 experiment"):
        remove_goal(project, "vo-backend")
    assert (project.goals_dir / "vo-backend.md").is_file()


def test_goal_rm_with_experiments_takes_the_subtree_but_not_the_branches(project):
    from hiraku_auto import gitops
    from hiraku_auto.scaffold import remove_goal

    gitops.create_branch(project.root, "exp/candidate")
    removal = remove_goal(project, "vo-backend", with_experiments=True)
    assert not (project.experiments_dir / "baseline").exists()
    assert not (project.experiments_dir / "candidate").exists()
    assert not (project.goals_dir / "vo-backend.md").exists()
    # Branches may hold committed work, so `rm` names them instead of destroying them.
    assert any("git branch -D exp/candidate" in note for note in removal.left_behind)


def test_removal_refuses_once_a_run_is_recorded(entrypoint):
    from hiraku_auto.launcher import launch
    from hiraku_auto.scaffold import remove_experiment, remove_goal

    launch(entrypoint, "candidate", "train", seed="1")

    with pytest.raises(HirakuError, match="append-only history"):
        remove_experiment(entrypoint, "candidate")
    with pytest.raises(HirakuError, match="append-only history"):
        remove_goal(entrypoint, "vo-backend", with_experiments=True)
    assert (entrypoint.experiments_dir / "candidate").is_dir()

    # --force is the way through, and the orphaned row is then visible in `check`.
    remove_experiment(entrypoint, "candidate", force=True)
    assert any("unknown experiment 'candidate'" in i for i in check(build(entrypoint)))


def test_exp_rm_refuses_to_orphan_its_children(project):
    from hiraku_auto.scaffold import remove_experiment

    with pytest.raises(HirakuError, match="1 descendant"):
        remove_experiment(project, "baseline")

    remove_experiment(project, "baseline", with_children=True)
    assert not (project.experiments_dir / "candidate").exists()


def test_exp_rm_of_a_leaf_resyncs_the_parent(project):
    from hiraku_auto.records import load_experiment
    from hiraku_auto.scaffold import remove_experiment

    remove_experiment(project, "candidate")
    assert "None." in load_experiment(project, "baseline").section("Children")
    assert check(build(project)) == []
