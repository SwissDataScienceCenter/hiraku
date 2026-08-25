from __future__ import annotations

import json

from hiraku_auto.cli import main


def run(capsys, *argv) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_init_creates_a_working_project(tmp_path, capsys):
    code, out, _ = run(capsys, "init", str(tmp_path / "new"))
    assert code == 0
    for name in (".hiraku.yml", "research/config.md", "research/leaderboard.md"):
        assert (tmp_path / "new" / name).is_file(), name
    # The agent workflow references are installed so an agent can load them locally.
    assert (tmp_path / "new" / ".agents/skills/hiraku-auto/triage.md").is_file()
    assert "project ready" in out


def test_init_refuses_to_clobber(tmp_path, capsys):
    run(capsys, "init", str(tmp_path / "new"))
    code, _, err = run(capsys, "init", str(tmp_path / "new"))
    assert code == 2 and "already exists" in err


def test_goal_and_experiment_lifecycle(bare_project, capsys):
    root = str(bare_project.root)
    assert run(capsys, "--project", root, "goal", "add", "g1", "--metric", "m")[0] == 0
    assert (
        run(capsys, "--project", root, "exp", "create", "e1", "--goal", "g1",
            "--expectation", "x")[0] == 0
    )
    code, out, _ = run(capsys, "--project", root, "exp", "list", "--json")
    rows = json.loads(out)
    assert rows[0]["experiment"] == "e1" and rows[0]["goal"] == "g1"

    assert run(capsys, "--project", root, "exp", "set", "e1", "--status", "concluded",
               "--verdict", "supports")[0] == 0
    code, out, _ = run(capsys, "--project", root, "exp", "list", "--json")
    assert json.loads(out)[0]["verdict"] == "supports"


def test_exp_create_without_goal_or_parent_is_rejected(bare_project, capsys):
    code, _, err = run(capsys, "--project", str(bare_project.root), "exp", "create", "x",
                       "--expectation", "y")
    assert code == 2 and "needs --goal" in err


def test_tree_json_is_a_full_snapshot(project, capsys):
    code, out, _ = run(capsys, "--project", str(project.root), "tree", "--json")
    data = json.loads(out)
    assert set(data) >= {"goals", "experiments", "runs", "issues"}
    assert data["experiments"]["candidate"]["parent"] == "baseline"


def test_check_is_quiet_on_a_clean_project(project, capsys):
    code, out, _ = run(capsys, "--project", str(project.root), "check")
    assert code == 0 and "no problems" in out


def test_check_strict_exits_non_zero(project, capsys):
    from hiraku_auto.records import load_experiment, save_experiment

    record = load_experiment(project, "candidate")
    record.front["status"] = "concluded"
    save_experiment(record)
    code, out, _ = run(capsys, "--project", str(project.root), "check", "--strict")
    assert code == 1 and "concluded without a verdict" in out


def test_runs_requires_a_sort_direction(entrypoint, capsys):
    code, _, err = run(capsys, "--project", str(entrypoint.root), "runs", "--sort", "ate_rmse_m")
    assert code == 2 and "exactly one of" in err


def test_launch_dry_run_executes_nothing(entrypoint, capsys):
    code, out, _ = run(capsys, "--project", str(entrypoint.root), "launch", "candidate:train",
                       "--dry-run")
    assert code == 0 and "would run" in out
    assert not [r for r in _rows(entrypoint)]


def test_launch_records_a_row(entrypoint, capsys):
    code, out, _ = run(capsys, "--project", str(entrypoint.root), "launch", "candidate:train",
                       "--commit", "HEAD", "--seed", "1")
    assert code == 0 and "reproducible" in out
    rows = _rows(entrypoint)
    assert len(rows) == 1 and rows[0]["experiment_id"] == "candidate"


def test_launch_target_must_name_an_entrypoint(entrypoint, capsys):
    code, _, err = run(capsys, "--project", str(entrypoint.root), "launch", "candidate")
    assert code == 2 and "<experiment>:<entrypoint>" in err


def test_dash_writes_a_self_contained_page(project, tmp_path, capsys):
    out_file = tmp_path / "d.html"
    code, out, _ = run(capsys, "--project", str(project.root), "dash", "-o", str(out_file))
    assert code == 0
    html = out_file.read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>")
    assert "http://" not in html and "https://" not in html  # nothing external
    assert "candidate" in html


def test_next_is_deterministic_and_needs_no_agent(project, capsys):
    code, out, _ = run(capsys, "--project", str(project.root), "next", "vo-backend", "--json")
    assert code == 0 and json.loads(out)["action"] == "launch"


def test_proposal_decline_keeps_the_file(project, capsys):
    from hiraku_auto.scaffold import create_proposal

    create_proposal(project, "an-idea", goal="vo-backend")
    code, out, _ = run(capsys, "--project", str(project.root), "proposal", "decline", "an-idea",
                       "--reason", "too costly")
    assert code == 0
    body = (project.proposals_dir / "an-idea.md").read_text(encoding="utf-8")
    assert "status: declined" in body and "too costly" in body


def test_proposal_accept_creates_an_experiment(project, capsys):
    from hiraku_auto.scaffold import create_proposal

    create_proposal(project, "an-idea", goal="vo-backend", proposed_parent="baseline",
                    hypothesis="It helps because of a mechanism.")
    code, _, _ = run(capsys, "--project", str(project.root), "proposal", "accept", "an-idea")
    assert code == 0
    body = (project.experiments_dir / "an-idea" / "experiment.md").read_text(encoding="utf-8")
    assert "origin: agent-literature" in body and "parent: baseline" in body


def test_unknown_project_is_a_clean_error(tmp_path, capsys):
    code, _, err = run(capsys, "--project", str(tmp_path), "check")
    assert code == 2 and "No .hiraku.yml" in err


def _rows(project):
    from hiraku_auto.runs import read_runs

    return read_runs(project.table_path).rows


def test_init_refuses_inside_the_tool_repo(tmp_path, capsys):
    """The mistake this prevents: init in the source tree writes research/ and .hiraku/ into it."""
    fake_tool = tmp_path / "hiraku"
    (fake_tool / "src" / "hiraku_auto").mkdir(parents=True)
    (fake_tool / "src" / "hiraku_auto" / "cli.py").write_text("", encoding="utf-8")

    code, _, err = run(capsys, "init", str(fake_tool))
    assert code == 2
    assert "inside the hiraku-auto source repository" in err
    assert "hiraku-research" in err            # suggests where to put it instead
    assert not (fake_tool / ".hiraku.yml").exists()
    assert not (fake_tool / "research").exists()

    # A subdirectory of the tool repo is refused too.
    code, _, err = run(capsys, "init", str(fake_tool / "my-research"))
    assert code == 2 and "source repository" in err
    assert not (fake_tool / "my-research").exists()  # no empty directory left behind


def test_init_in_the_tool_repo_can_be_forced(tmp_path, capsys):
    fake_tool = tmp_path / "hiraku"
    (fake_tool / "src" / "hiraku_auto").mkdir(parents=True)
    (fake_tool / "src" / "hiraku_auto" / "cli.py").write_text("", encoding="utf-8")
    code, _, _ = run(capsys, "init", str(fake_tool), "--allow-in-tool-repo")
    assert code == 0 and (fake_tool / ".hiraku.yml").is_file()


def test_init_git_creates_the_repository(tmp_path, capsys):
    from hiraku_auto import gitops

    target = tmp_path / "natix-research"
    code, out, _ = run(capsys, "init", str(target), "--git", "--name", "NATIX")
    assert code == 0
    assert gitops.is_repo(target)
    assert "git repository initialised" in out


def test_init_without_git_says_so(tmp_path, capsys):
    code, out, _ = run(capsys, "init", str(tmp_path / "plain"))
    assert code == 0 and "not a git repository" in out
