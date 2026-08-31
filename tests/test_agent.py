"""The agent driver, with a fake agent command.

No test here calls a real model. A stub script stands in for `claude -p`, so the parts under test
are the ones this package owns: prompt assembly, JSON extraction, and what gets written.
"""

from __future__ import annotations

import json
import stat
import sys

import pytest

from hiraku_auto import agent as agent_mod
from hiraku_auto.errors import AgentError
from hiraku_auto.records import load_experiment
from hiraku_auto.tree import build


def fake_agent(tmp_path, reply: str):
    """A script that ignores its prompt and prints a fixed reply."""
    script = tmp_path / "fake-agent.py"
    script.write_text(
        "import sys\nsys.stdout.write(open(sys.argv[0] + '.reply').read())\n", encoding="utf-8"
    )
    (tmp_path / "fake-agent.py.reply").write_text(reply, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return [sys.executable, str(script)]


# --------------------------------------------------------------------------- json extraction


def test_extract_plain_object():
    assert agent_mod.extract_json('{"a": 1}') == {"a": 1}


def test_extract_from_a_fenced_block():
    assert agent_mod.extract_json('here you go\n```json\n{"a": 2}\n```\nthanks') == {"a": 2}


def test_extract_unwraps_the_claude_json_envelope():
    envelope = json.dumps({"type": "result", "result": '{"a": 3}'})
    assert agent_mod.extract_json(envelope) == {"a": 3}


def test_extract_handles_braces_inside_strings():
    text = '{"a": "not } the end", "b": 1}'
    assert agent_mod.extract_json(text) == {"a": "not } the end", "b": 1}


def test_extract_rejects_non_json():
    with pytest.raises(AgentError, match="did not return a JSON object"):
        agent_mod.extract_json("I would rather write you an essay.")


def test_missing_agent_binary_is_a_clear_error():
    with pytest.raises(AgentError, match="not found on PATH"):
        agent_mod.call_agent("hi", command=["definitely-not-installed-xyz"])


def test_agent_command_is_configurable(monkeypatch):
    monkeypatch.setenv("HIRAKU_AGENT_CMD", "codex exec --json")
    assert agent_mod.agent_command() == ["codex", "exec", "--json"]
    assert agent_mod.agent_command("claude -p") == ["claude", "-p"]


# --------------------------------------------------------------------------- prompts


def test_prompt_carries_the_rules_the_state_and_no_placeholders(project):
    forest = build(project)
    prompt = agent_mod.build_prompt(
        "triage", agent_mod.slim_state(forest, focus="candidate"), project=project
    )
    assert "investigation test" in prompt.lower()
    assert '"focus_experiment": "candidate"' in prompt
    assert "{{" not in prompt


def test_state_is_scoped_to_one_goal(project):
    from hiraku_auto.scaffold import create_experiment, create_goal

    create_goal(project, "other-goal", primary_metric="x")
    create_experiment(project, "elsewhere", goal="other-goal", expectation="x")
    state = agent_mod.slim_state(build(project), goal="vo-backend")
    assert "elsewhere" not in state["experiments"]
    assert set(state["experiments"]) == {"baseline", "candidate"}


def test_dry_run_calls_nothing(project):
    result = agent_mod.run_triage(project, "candidate", dry_run=True)
    assert result.prompt and result.raw == "" and result.written == []


# --------------------------------------------------------------------------- applying replies


def test_triage_concludes_the_node_and_creates_a_child(project, tmp_path):
    reply = json.dumps(
        {
            "blocked": None,
            "failure_class": "C",
            "class_evidence": "seed spread swamps the effect",
            "verdict": "inconclusive",
            "findings": "two seeds, spread 0.4",
            "conclusions": "cannot separate noise from effect",
            "investigation_test": {"named_cause": {"pass": True, "note": "variance"}},
            "proceed": True,
            "blocking_condition": None,
            "children": [
                {
                    "id": "more-seeds",
                    "kind": "diagnostic",
                    "expectation": "Five seeds separate the two explanations.",
                    "scope": "responds to the spread in the parent",
                }
            ],
        }
    )
    agent_mod.run_triage(project, "candidate", command=fake_agent(tmp_path, reply))

    parent = load_experiment(project, "candidate")
    assert parent.front["status"] == "concluded"
    assert parent.front["verdict"] == "inconclusive"
    assert "spread 0.4" in parent.section("Findings")
    assert "named_cause" in parent.section("Decisions")

    child = load_experiment(project, "more-seeds")
    assert child.front["parent"] == "candidate"
    assert child.front["origin"] == "agent-diagnostic"
    assert child.front["kind"] == "diagnostic"
    # A child inherits the parent's mode, never a wider one.
    assert child.front["mode"] == parent.front["mode"]


def test_triage_of_a_class_a_defect_sets_no_verdict_and_no_child(project, tmp_path):
    reply = json.dumps(
        {
            "failure_class": "A",
            "class_evidence": "exit code 1",
            "verdict": None,
            "findings": "the run crashed",
            "conclusions": "the expectation was never tested",
            "investigation_test": {},
            "proceed": False,
            "blocking_condition": None,
            "children": [{"id": "should-not-exist", "kind": "diagnostic"}],
        }
    )
    agent_mod.run_triage(project, "candidate", command=fake_agent(tmp_path, reply))
    record = load_experiment(project, "candidate")
    assert "verdict" not in record.front or not record.front["verdict"]
    assert record.front["status"] != "concluded"
    assert not (project.experiments_dir / "should-not-exist").exists()


def test_a_blocked_reply_writes_nothing(project, tmp_path):
    reply = json.dumps({"blocked": "no expectation recorded"})
    result = agent_mod.run_triage(project, "candidate", command=fake_agent(tmp_path, reply))
    assert result.data["blocked"]
    assert result.written == []


def test_no_apply_writes_nothing(project, tmp_path):
    reply = json.dumps(
        {"failure_class": "B", "verdict": "refutes", "findings": "x", "conclusions": "y",
         "proceed": False, "children": [], "investigation_test": {}}
    )
    result = agent_mod.run_triage(
        project, "candidate", command=fake_agent(tmp_path, reply), apply=False
    )
    assert result.written == []
    assert load_experiment(project, "candidate").front["status"] != "concluded"


def test_design_creates_proposed_nodes(project, tmp_path):
    reply = json.dumps(
        {
            "experiments": [
                {
                    "id": "new-idea",
                    "kind": "hypothesis",
                    "parent": "baseline",
                    "expectation": "It lowers the metric by a stated mechanism.",
                    "scope": "scope text",
                    "expectation_body": "prediction, then refutation",
                    "rationale": "because the baseline leaves this open",
                }
            ],
            "notes": None,
        }
    )
    agent_mod.run_design(project, "vo-backend", command=fake_agent(tmp_path, reply))
    record = load_experiment(project, "new-idea")
    assert record.front["status"] == "proposed"
    assert record.front["parent"] == "baseline"
    assert "refutation" in record.section("Expectation")
    assert "because the baseline" in record.section("Decisions")


def test_discover_writes_proposals_not_experiments(project, tmp_path):
    reply = json.dumps(
        {
            "proposals": [
                {
                    "id": "from-a-paper",
                    "kind": "hypothesis",
                    "proposed_parent": "baseline",
                    "title": "From a paper",
                    "hypothesis": "h",
                    "provenance": "p",
                    "applicability": "a",
                    "prediction": "pr",
                    "investigation_test": "it",
                    "cost": "c",
                    "counterargument": "against",
                }
            ]
        }
    )
    agent_mod.run_discover(project, "vo-backend", command=fake_agent(tmp_path, reply))
    assert (project.proposals_dir / "from-a-paper.md").is_file()
    assert not (project.experiments_dir / "from-a-paper").exists()
    body = (project.proposals_dir / "from-a-paper.md").read_text(encoding="utf-8")
    assert "against" in body and "origin: agent-literature" in body


def test_the_prompt_goes_on_stdin_for_claude_and_after_the_argv_for_others():
    deliver = agent_mod.prompt_delivery
    # A variadic --add-dir would eat a trailing prompt, so claude gets it on stdin.
    argv, stdin = deliver(["claude", "-p", "--add-dir", "../code"], "QUESTION")
    assert argv == ["claude", "-p", "--add-dir", "../code"] and stdin == "QUESTION"
    # Anything else keeps the old contract.
    argv, stdin = deliver(["codex", "exec"], "QUESTION")
    assert argv == ["codex", "exec", "QUESTION"] and stdin is None
    # An explicit placeholder wins over both.
    argv, stdin = deliver(["claude", "-p", "{prompt}", "--add-dir", "../code"], "QUESTION")
    assert argv == ["claude", "-p", "QUESTION", "--add-dir", "../code"] and stdin is None


def test_a_claude_like_command_is_fed_through_stdin(project, tmp_path):
    """End to end: a command named `claude` that only reads stdin still gets its prompt."""
    script = tmp_path / "claude"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "text = sys.stdin.read()\n"
        "sys.exit('Input must be provided either through stdin') if not text.strip() else None\n"
        'print(\'{"proposals": [{"id": "via-stdin", "title": "T", "hypothesis": "h"}]}\')\n',
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    agent_mod.run_discover(project, "vo-backend", command=[str(script), "-p", "--add-dir", ".."])
    assert (project.proposals_dir / "via-stdin.md").is_file()


def test_a_swallowed_prompt_is_diagnosed():
    advice = agent_mod.diagnose("Error: Input must be provided either through stdin or as a prompt")
    assert "--add-dir" in advice and "{prompt}" in advice


def test_web_allowed_reads_the_command(tmp_path):
    allowed = agent_mod.web_allowed
    assert not allowed(["claude", "-p"])
    assert not allowed(["claude", "-p", "--allowedTools", "Read,Glob,Grep"])
    assert allowed(["claude", "-p", "--allowedTools", "WebSearch,WebFetch,Read"])
    assert allowed(["/usr/local/bin/claude", "-p", "--allowedTools", "webfetch"])
    assert allowed(["claude", "-p", "--dangerously-skip-permissions"])
    # Another agent entirely: not ours to second-guess.
    assert allowed(["codex", "exec"])
    assert not allowed([])


def test_a_discover_call_without_the_web_marks_its_proposals(project, tmp_path):
    reply = json.dumps(
        {
            "proposals": [
                {
                    "id": "from-a-paper",
                    "title": "From a paper",
                    "hypothesis": "h",
                    "provenance": "Zhang et al. 2024 report a 12% gain.",
                    "prediction": "pr",
                    "investigation_test": "it",
                }
            ],
            "notes": "WebSearch/WebFetch permission was requested and denied this session.",
        }
    )
    result = agent_mod.run_discover(project, "vo-backend", command=fake_agent(tmp_path, reply))
    assert result.data["citations_unverified"] is True
    body = (project.proposals_dir / "from-a-paper.md").read_text(encoding="utf-8")
    assert "UNVERIFIED" in body
    # The marker sits with the citations it qualifies, not at the top of the file.
    assert body.index("## Where it came from") < body.index("UNVERIFIED")
    assert "Zhang et al. 2024" in body


def test_a_discover_call_that_could_search_marks_nothing(project, tmp_path):
    reply = json.dumps(
        {"proposals": [{"id": "clean", "title": "Clean", "hypothesis": "h", "provenance": "p"}]}
    )
    result = agent_mod.run_discover(project, "vo-backend", command=fake_agent(tmp_path, reply))
    assert not result.data["citations_unverified"]
    assert "UNVERIFIED" not in (project.proposals_dir / "clean.md").read_text(encoding="utf-8")


def test_a_researchers_own_idea_becomes_a_proposal_marked_as_theirs(project, tmp_path):
    reply = json.dumps(
        {
            "proposals": [
                {
                    "id": "dinov3-retrieval-prefilter",
                    "origin": "researcher",
                    "title": "Cheap appearance prefilter",
                    "hypothesis": "h",
                    "provenance": "The researcher's own note, plus AnyLoc-style evidence.",
                },
                {"id": "found-by-search", "origin": "agent-literature", "title": "T",
                 "hypothesis": "h"},
                {"id": "unknown-origin", "origin": "nonsense", "title": "T", "hypothesis": "h"},
            ]
        }
    )
    agent_mod.run_discover(
        project,
        "vo-backend",
        instruction="I have five directions; the first is a DINOv3 retrieval prefilter.",
        command=fake_agent(tmp_path, reply),
    )
    mine = (project.proposals_dir / "dinov3-retrieval-prefilter.md").read_text(encoding="utf-8")
    assert "origin: researcher" in mine
    theirs = (project.proposals_dir / "found-by-search.md").read_text(encoding="utf-8")
    assert "origin: agent-literature" in theirs
    # An origin outside the vocabulary is not written into a record.
    unknown = (project.proposals_dir / "unknown-origin.md").read_text(encoding="utf-8")
    assert "origin: agent-literature" in unknown


def test_the_instruction_is_labelled_as_the_researchers(project):
    prompt = agent_mod.build_prompt(
        "discover", {"goals": {}}, instruction="try the cheap prefilter first"
    )
    assert "## Instruction from the researcher" in prompt
    assert "try the cheap prefilter first" in prompt
    assert "## Instruction from the researcher" not in agent_mod.build_prompt(
        "discover", {"goals": {}}
    )


def test_a_declined_idea_becomes_a_record_not_a_terminal_note(project, tmp_path):
    reply = json.dumps(
        {
            "proposals": [],
            "declined": [
                {
                    "id": "dinov3-retrieval-pair-graph",
                    "origin": "researcher",
                    "kind": "diagnostic",
                    "title": "Candidate retrieval prefilter",
                    "hypothesis": "A cheap DINOv3 cosine prefilter removes obviously-wrong "
                                  "candidates before VGGT runs.",
                    "failing_condition": "4 — in scope",
                    "reason": "`### Held fixed` forbids regenerating P, and every cross pair's "
                              "candidate is fixed at construction time.",
                    "belongs_under": "A goal whose primary metric is VGGT calls per confirmed "
                                     "match — an efficiency question, not an accuracy one.",
                }
            ],
        }
    )
    result = agent_mod.run_discover(project, "vo-backend", command=fake_agent(tmp_path, reply))
    assert result.data["declined_written"] == ["dinov3-retrieval-pair-graph"]
    body = (project.proposals_dir / "dinov3-retrieval-pair-graph.md").read_text(encoding="utf-8")
    assert "status: declined" in body and "origin: researcher" in body
    assert "decided_on:" in body
    # The reasoning is in the record, where an argument with it can happen.
    assert "4 — in scope" in body and "Held fixed" in body
    assert "belongs under a different goal" in body and "efficiency question" in body
    assert "A cheap DINOv3 cosine prefilter" in body


def test_rerunning_discover_keeps_the_proposals_that_are_already_there(project, tmp_path):
    reply = json.dumps(
        {
            "proposals": [
                {"id": "already-here", "title": "Old", "hypothesis": "h"},
                {"id": "brand-new", "title": "New", "hypothesis": "h"},
            ]
        }
    )
    (project.proposals_dir).mkdir(parents=True, exist_ok=True)
    kept = project.proposals_dir / "already-here.md"
    kept.write_text("---\nid: already-here\nstatus: declined\n---\n\n# Declined\n", "utf-8")

    result = agent_mod.run_discover(project, "vo-backend", command=fake_agent(tmp_path, reply))
    # The decision recorded in the declined proposal survives, and the batch still completes.
    assert kept.read_text(encoding="utf-8").count("status: declined") == 1
    assert result.data["skipped_existing"] == ["already-here"]
    assert (project.proposals_dir / "brand-new.md").is_file()


def test_a_denied_tool_permission_is_diagnosed():
    advice = agent_mod.diagnose("Claude requested permissions to use WebSearch, but you haven't")
    assert "--allowedTools" in advice and "config.md" in advice


def test_malformed_reply_is_reported(project, tmp_path):
    with pytest.raises(AgentError, match="missing a valid 'experiments'"):
        agent_mod.run_design(project, "vo-backend", command=fake_agent(tmp_path, '{"oops": 1}'))


# --------------------------------------------------------------------------- the loop's ordering


def test_suggest_next_prefers_launching_an_approved_node(project):
    assert agent_mod.suggest_next(build(project), "vo-backend")["action"] == "launch"


def test_suggest_next_asks_for_a_decision_when_only_proposals_remain(project):
    from hiraku_auto.records import save_experiment

    for node in ("baseline", "candidate"):
        record = load_experiment(project, node)
        record.front["status"] = "proposed"
        save_experiment(record)
    suggestion = agent_mod.suggest_next(build(project), "vo-backend")
    assert suggestion["action"] == "approve"
    assert set(suggestion["pending"]) == {"baseline", "candidate"}


def test_suggest_next_reports_exhaustion(project):
    from hiraku_auto.records import save_experiment

    for node in ("baseline", "candidate"):
        record = load_experiment(project, node)
        record.front["status"] = "concluded"
        record.front["verdict"] = "refutes"
        record.set_section("Children", "None.")
        save_experiment(record)
    # Both concluded and both already triaged (no children to add), nothing proposed.
    forest = build(project)
    forest.nodes["baseline"].children = ["candidate"]
    forest.nodes["candidate"].children = ["baseline"]
    assert agent_mod.suggest_next(forest, "vo-backend")["action"] == "exhausted"


def test_an_empty_goal_is_not_an_exhausted_one(bare_project):
    """The state right after `goal add`: reporting "exhausted" there would read as a finding."""
    from hiraku_auto.scaffold import create_goal

    create_goal(bare_project, "brand-new", primary_metric="err")
    suggestion = agent_mod.suggest_next(build(bare_project), "brand-new")
    assert suggestion["action"] == "design"
    assert "no experiments yet" in suggestion["why"]
    assert any("agent design" in c for c in suggestion["next_commands"])


def test_suggest_next_rejects_an_unknown_goal(project):
    import pytest as _pytest

    from hiraku_auto.errors import HirakuError

    with _pytest.raises(HirakuError, match="does not exist"):
        agent_mod.suggest_next(build(project), "no-such-goal")
