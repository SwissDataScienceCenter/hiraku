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
    (bare_project.root / "CLAUDE.md").write_text("x" * 80_000, "utf-8")
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


# --------------------------------------------------------------------------- progress and logs


def test_every_call_is_logged_with_its_prompt_and_reply(project, tmp_path):
    reply = json.dumps({"experiments": [], "notes": "nothing to add"})
    result = agent_mod.run_design(project, "vo-backend", command=fake_agent(tmp_path, reply))

    assert result.log is not None and result.log.is_dir()
    assert "vo-backend" in result.log.name and "design" in result.log.name
    assert "investigation test" in (result.log / "prompt.md").read_text().lower()
    assert (result.log / "reply.txt").read_text() == result.raw

    meta = json.loads((result.log / "meta.json").read_text())
    assert meta["workflow"] == "design" and meta["target"] == "vo-backend"
    assert meta["error"] is None and meta["seconds"] >= 0

    entries = agent_mod.read_logs(project)
    assert entries[0]["id"] == result.log.name


def test_a_failed_call_still_leaves_the_prompt_and_the_reason(project, tmp_path):
    from hiraku_auto.errors import AgentError

    with pytest.raises(AgentError):
        agent_mod.run_design(project, "vo-backend", command=fake_agent(tmp_path, "not json"))

    entry = agent_mod.read_logs(project)[0]
    assert entry["error"] is None  # the call itself succeeded; extraction is what failed
    assert (entry["path"] / "prompt.md").is_file()


def test_hooks_fire_around_the_call(project, tmp_path):
    seen: dict[str, object] = {}
    hooks = agent_mod.Hooks(
        start=lambda info: seen.setdefault("start", info),
        tick=lambda elapsed: seen.setdefault("ticked", True),
        done=lambda info: seen.setdefault("done", info),
    )
    agent_mod.run_design(
        project,
        "vo-backend",
        command=fake_agent(tmp_path, json.dumps({"experiments": []})),
        hooks=hooks,
    )
    start = seen["start"]
    assert start["workflow"] == "design" and start["target"] == "vo-backend"
    assert start["prompt_chars"] > 0 and start["log"].endswith("-design-vo-backend")
    assert seen["done"]["error"] is None


def test_a_slow_agent_ticks_and_a_hung_one_times_out(project, tmp_path):
    import stat
    import sys as _sys

    from hiraku_auto.errors import AgentError

    script = tmp_path / "sleeper.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    ticks: list[float] = []
    with pytest.raises(AgentError, match="timed out after 1s"):
        agent_mod.call_agent(
            "hi",
            command=[_sys.executable, str(script)],
            timeout=1,
            progress=ticks.append,
        )
    assert ticks, "the caller must hear about a wait it cannot see"


def test_a_reply_larger_than_the_pipe_buffer_does_not_deadlock(project, tmp_path):
    """Draining on threads is what makes this safe; `run` with a poll loop would hang."""
    big = json.dumps({"experiments": [], "notes": "x" * 200_000})
    result = agent_mod.run_design(project, "vo-backend", command=fake_agent(tmp_path, big))
    assert len(result.raw) > 200_000


def test_init_gitignores_bytecode_and_caches(bare_project):
    """A .pyc in history is a binary diff nobody reads that conflicts on every merge."""
    text = (bare_project.root / ".gitignore").read_text(encoding="utf-8")
    for entry in ("__pycache__/", "*.py[cod]", ".pytest_cache/", ".ruff_cache/", ".DS_Store"):
        assert entry in text


# --------------------------------------------------------------------------- undefined metrics


UNDEFINED = {
    **DRAFT,
    "success_criterion": "`reprojection_error_px_median` falls by 15%, `kept_frame_rate` holds, "
                         "and `pairs_at_cap_fraction` does not increase.",
    "evaluation_protocol": {
        "dataset": "d", "splits": "s",
        "metrics": "`reprojection_error_px_median` is the median pixel L2. "
                   "`kept_frame_rate` is kept frames over 2700.",
        "seeds": "three", "held_fixed": "intrinsics",
    },
}


def test_a_metric_used_only_in_the_criterion_is_reported(bare_project, tmp_path):
    """The real failure: a criterion that reads as precise, naming a quantity nothing defines."""
    from hiraku_auto.records import undefined_metrics

    agent_mod.run_draft(
        bare_project, "vggt-pose", "q", command=fake_agent(tmp_path, json.dumps(UNDEFINED))
    )
    record = load_goal(bare_project, "vggt-pose")
    undefined = undefined_metrics(record)
    assert [m for m, _ in undefined] == ["pairs_at_cap_fraction"]
    assert "Success criterion" in undefined[0][1]

    issues = check(build(bare_project))
    assert any("pairs_at_cap_fraction" in i and "never defined" in i for i in issues)


def test_a_metric_defined_in_the_protocol_is_not_reported(bare_project, tmp_path):
    from hiraku_auto.records import undefined_metrics

    ok = {**UNDEFINED}
    ok["evaluation_protocol"] = {
        **UNDEFINED["evaluation_protocol"],
        "metrics": UNDEFINED["evaluation_protocol"]["metrics"]
        + " `pairs_at_cap_fraction` is the share of evaluation pairs whose match count hits the "
          "per-pair cap of 2048; it rises when the matcher saturates.",
    }
    agent_mod.run_draft(
        bare_project, "vggt-pose", "q", command=fake_agent(tmp_path, json.dumps(ok))
    )
    assert undefined_metrics(load_goal(bare_project, "vggt-pose")) == []
    assert check(build(bare_project)) == []


def test_the_front_matter_metrics_must_be_defined_too(bare_project, tmp_path):
    from hiraku_auto.records import undefined_metrics

    bad = {**DRAFT, "evaluation_protocol": {
        **DRAFT["evaluation_protocol"], "metrics": "Some numbers are computed."}}
    agent_mod.run_draft(
        bare_project, "vggt-pose", "q", command=fake_agent(tmp_path, json.dumps(bad))
    )
    names = {m for m, _ in undefined_metrics(load_goal(bare_project, "vggt-pose"))}
    assert "reprojection_error_px_median" in names
    assert "kept_frame_rate" in names


def test_tool_vocabulary_is_not_mistaken_for_a_metric(project):
    """`run_kind` and friends are the tool's own words, not measurements."""
    from hiraku_auto.records import load_goal as _load
    from hiraku_auto.records import undefined_metrics

    record = _load(project, "vo-backend")
    record.set_section(
        "Out of scope",
        "Runs with `run_kind` worktree. Anything needing a different `agreement_tolerance` "
        "or a second `primary_metric`.",
    )
    record.path.write_text(record.render(), encoding="utf-8")
    names = {m for m, _ in undefined_metrics(_load(project, "vo-backend"))}
    assert not names & {"run_kind", "agreement_tolerance", "primary_metric"}


# --------------------------------------------------------------------------- truncation


def big_memory_file(root: Path, headings: list[str], per_section: int = 4000) -> Path:
    parts = ["# project\n\nOverview.\n"]
    for name in headings:
        parts.append(f"\n## {name}\n\n" + ("x" * per_section) + "\n")
    path = root / "CLAUDE.md"
    path.write_text("".join(parts), encoding="utf-8")
    return path


def test_truncation_cuts_on_a_heading_and_names_what_was_dropped(bare_project):
    """"first 20,000 of 167,131" tells the agent nothing. A list of section titles does."""
    big_memory_file(bare_project.root, ["Conventions", "Data", "Preprocessing", "Intrinsics"])
    config = read_record(bare_project.config_md)
    config.front["context"] = {"sources": ["CLAUDE.md"], "max_bytes_per_file": 9000}
    config.path.write_text(config.render(), encoding="utf-8")

    entry = context_mod.collect(bare_project).files[0]
    assert entry.truncated
    assert entry.dropped, "the dropped headings must be named"
    assert "Preprocessing" in entry.dropped and "Intrinsics" in entry.dropped
    # every section that IS included is included whole
    assert entry.text.count("x" * 4000) == entry.text.count("## ")

    payload = entry.to_dict()
    assert payload["sections_not_included"] == entry.dropped
    assert "do not guess" in payload["how_to_handle_the_gap"]


def test_a_file_that_fits_is_not_reported_as_truncated(bare_project):
    """A trailing newline used to make a whole file look cut by one character."""
    (bare_project.root / "CLAUDE.md").write_text("# p\n\nshort, and ends in a newline\n", "utf-8")
    entry = next(f for f in context_mod.collect(bare_project).files if f.rel == "CLAUDE.md")
    assert not entry.truncated and entry.dropped == []
    assert not context_mod.collect(bare_project).truncated_files


def test_a_file_with_no_headings_falls_back_to_a_byte_cut(bare_project):
    (bare_project.root / "CLAUDE.md").write_text("y" * 30_000, "utf-8")
    config = read_record(bare_project.config_md)
    config.front["context"] = {"sources": ["CLAUDE.md"], "max_bytes_per_file": 5000}
    config.path.write_text(config.render(), encoding="utf-8")
    entry = context_mod.collect(bare_project).files[0]
    assert entry.truncated and entry.dropped == []
    assert len(entry.text) <= 5000


def test_the_bundle_reports_the_caps_that_would_fit_everything(bare_project):
    big_memory_file(bare_project.root, ["A", "B", "C"], per_section=8000)
    config = read_record(bare_project.config_md)
    config.front["context"] = {"sources": ["CLAUDE.md"], "max_bytes_per_file": 5000}
    config.path.write_text(config.render(), encoding="utf-8")
    bundle = context_mod.collect(bare_project)
    per_file, total = bundle.needed_limits()
    assert per_file == bundle.files[0].bytes_total
    assert total >= per_file
    assert "truncated" in bundle.summary()


def test_max_bytes_raises_the_cap_for_one_call_only(bare_project):
    big_memory_file(bare_project.root, ["A", "B", "C"], per_section=8000)
    config = read_record(bare_project.config_md)
    config.front["context"] = {"sources": ["CLAUDE.md"], "max_bytes_per_file": 5000}
    config.path.write_text(config.render(), encoding="utf-8")

    assert context_mod.collect(bare_project).files[0].truncated
    whole = context_mod.collect(bare_project, max_bytes=200_000).files[0]
    assert not whole.truncated
    # the total cap rises with it — a per-file cap you cannot use is not a cap
    assert context_mod.collect(bare_project).files[0].truncated, "the config is unchanged"


def test_the_agent_call_can_raise_the_cap(project):
    big_memory_file(project.root, ["Conventions", "Preprocessing"], per_section=9000)
    config = read_record(project.config_md)
    config.front["context"] = {"sources": ["CLAUDE.md"], "max_bytes_per_file": 6000}
    config.path.write_text(config.render(), encoding="utf-8")

    default = agent_mod.run_design(project, "vo-backend", dry_run=True)
    assert "sections_not_included" in default.prompt

    whole = agent_mod.run_design(project, "vo-backend", dry_run=True, context_bytes=100_000)
    assert "sections_not_included" not in whole.prompt
    assert not whole.context.truncated_files


def test_a_trailing_comment_on_a_cap_still_parses_as_a_number():
    """`max_bytes_per_file: 60000  # why` must be 60000, not a string, without PyYAML."""
    text = (
        "context:\n"
        "  max_files: 40\n"
        "  max_bytes_per_file: 60000     # a bigger file is cut on a heading boundary\n"
        "  max_total_bytes: 400000       # names the sections it left out\n"
        '  note: "a # inside quotes is kept"\n'
    )
    parsed = yamlio._fallback_load(text)["context"]
    assert parsed["max_bytes_per_file"] == 60000
    assert parsed["max_total_bytes"] == 400000
    assert parsed["note"] == "a # inside quotes is kept"
