from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from hiraku.config import HirakuConfig

SCRIPTS = Path(__file__).parents[1] / ".ai/skills/hiraku-powers/scripts"

pytestmark = pytest.mark.skipif(
    shutil.which("jq") is None or shutil.which("bash") is None,
    reason="hiraku-powers scripts need bash and jq",
)


def _env() -> dict[str, str]:
    # Put this environment's `hiraku` entry point first on PATH.
    bin_dir = str(Path(sys.executable).parent)
    return {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}


def _run(project: Path, name: str, *args: str, check: bool = True):
    return subprocess.run(
        ["bash", str(SCRIPTS / name), *args],
        cwd=project,
        env=_env(),
        text=True,
        capture_output=True,
        check=check,
    )


def _set_body(project: Path, text: str) -> None:
    path = project / "body.md"
    path.write_text(text)
    _hiraku(project, "record", "update", "r1", "--markdown-file", str(path))


def _hiraku(project: Path, *args: str) -> str:
    return subprocess.run(
        ["hiraku", *args],
        cwd=project,
        env=_env(),
        text=True,
        capture_output=True,
        check=True,
    ).stdout


def _body(project: Path, record_id: str) -> str:
    return json.loads(_hiraku(project, "record", "show", record_id, "--json"))[
        "markdown"
    ]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    for command in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@example.invalid"],
        ["git", "config", "user.name", "T"],
        ["git", "commit", "-q", "--allow-empty", "-m", "init"],
    ):
        subprocess.run(command, cwd=root, check=True)
    HirakuConfig.init(root, install_skill=False)
    _hiraku(root, "record", "create", "r1", "--type", "task", "--set", "goal=g")
    return root


def test_workspace_is_seeded_ignored_and_stable(project: Path) -> None:
    body = project / "body.md"
    body.write_text("## Notes\nkeep\n\n```\n## Summary\n```\n")
    _hiraku(project, "record", "update", "r1", "--markdown-file", str(body))

    workspace = Path(_run(project, "workspace", "r1").stdout.strip())

    assert workspace == project / ".hiraku-powers/records/r1"
    assert (workspace / "ledger.md").is_file()
    assert "keep" in (workspace / "rest.md").read_text()
    # A fenced "## Summary" is content, not a section.
    assert not (workspace / "summary.md").read_text().strip()
    status = subprocess.run(
        ["git", "status", "--short"], cwd=project, text=True, capture_output=True
    ).stdout
    assert ".hiraku-powers" not in status


def test_push_assembles_sections_without_accumulating_blank_lines(
    project: Path,
) -> None:
    workspace = Path(_run(project, "workspace", "r1").stdout.strip())
    (workspace / "summary.md").write_text("## Summary\n\n- Status: proposed\n")
    (workspace / "plan.md").write_text("## Plan\n\n- [ ] 1\n")

    for _ in range(3):
        _run(project, "push", "r1")

    body = _body(project, "r1")
    assert body.startswith("## Summary\n\n- Status: proposed\n\n## Plan\n")
    assert "\n\n\n" not in body


def test_push_refuses_stale_workspace_unless_forced(project: Path) -> None:
    workspace = Path(_run(project, "workspace", "r1").stdout.strip())
    (workspace / "summary.md").write_text("## Summary\n\n- Status: proposed\n")
    _run(project, "push", "r1")
    edited = project / "edited.md"
    edited.write_text("## Notes\nedited elsewhere\n")
    _hiraku(project, "record", "update", "r1", "--markdown-file", str(edited))

    stale = _run(project, "push", "r1", check=False)

    assert stale.returncode == 4
    assert "changed since pull" in stale.stderr
    assert "edited elsewhere" in _body(project, "r1")
    _run(project, "push", "r1", "--force")
    assert "## Summary" in _body(project, "r1")


def test_push_requires_summary_heading(project: Path) -> None:
    workspace = Path(_run(project, "workspace", "r1").stdout.strip())
    (workspace / "summary.md").write_text("- no heading\n")

    result = _run(project, "push", "r1", check=False)

    assert result.returncode == 3
    assert "## Summary" in result.stderr


FENCED = (
    "~~~\n## Plan\nnot a section\n~~~\n\n````\n```\n## Summary\nstill code\n```\n````\n"
)


def test_fences_of_any_style_survive_a_round_trip(project: Path) -> None:
    _set_body(project, "# Title\n\nintro\n\n## Notes\n" + FENCED)
    workspace = Path(_run(project, "workspace", "r1").stdout.strip())
    (workspace / "summary.md").write_text("## Summary\n\n- Status: proposed\n")

    _run(project, "push", "r1")

    body = _body(project, "r1")
    assert body.startswith("# Title\n\nintro\n\n## Summary\n")
    assert FENCED.strip() in body
    assert body.count("## Summary\n\n- Status: proposed") == 1
    assert body.endswith(FENCED)


def test_headings_with_trailing_space_or_crlf_are_recognised(project: Path) -> None:
    _set_body(project, "## Summary  \r\n\r\n- Status: proposed\r\n")

    workspace = Path(_run(project, "workspace", "r1").stdout.strip())
    _run(project, "push", "r1")

    assert "proposed" in (workspace / "summary.md").read_text()
    assert _body(project, "r1").count("## Summary") == 1


def test_failed_seed_does_not_disable_protection(project: Path) -> None:
    failed = _run(project, "ledger", "later", "msg", check=False)
    assert failed.returncode != 0
    assert not (project / ".hiraku-powers/records/later").exists()

    _hiraku(project, "record", "create", "later", "--type", "task", "--set", "goal=g")
    body = project / "body.md"
    body.write_text("## Notes\nprecious\n")
    _hiraku(project, "record", "update", "later", "--markdown-file", str(body))
    workspace = Path(_run(project, "workspace", "later").stdout.strip())
    (workspace / "summary.md").write_text("## Summary\n\n- Status: proposed\n")
    _run(project, "push", "later")

    assert "precious" in _body(project, "later")


def test_unseeded_workspace_cannot_push(project: Path) -> None:
    workspace = project / ".hiraku-powers/records/r1"
    workspace.mkdir(parents=True)
    (workspace / "summary.md").write_text("## Summary\n")

    result = _run(project, "push", "r1", check=False)

    assert result.returncode == 4
    assert "not seeded" in result.stderr


def test_path_like_ids_create_nothing(project: Path) -> None:
    for bad in ("../../escape", "nosuch"):
        result = _run(project, "workspace", bad, check=False)
        assert result.returncode != 0
    assert not (project.parent / "escape").exists()
    assert not (project / ".hiraku-powers/records/nosuch").exists()


def test_pull_protects_unpushed_edits(project: Path) -> None:
    workspace = Path(_run(project, "workspace", "r1").stdout.strip())
    (workspace / "summary.md").write_text("## Summary\n\n- Status: draft\n")

    refused = _run(project, "pull", "r1", check=False)
    scratch = project / "scratch"
    _run(project, "pull", "r1", "--to", str(scratch))

    assert refused.returncode == 4
    assert "draft" in (workspace / "summary.md").read_text()
    assert (scratch / "summary.md").exists()
    _run(project, "pull", "r1", "--discard")
    assert "draft" not in (workspace / "summary.md").read_text()


@pytest.mark.parametrize(
    ("part", "text", "message"),
    [
        ("plan", "## Plan\n\n## Tasks\n- x\n", "exactly one"),
        ("plan", "no heading\n", "exactly one"),
        ("rest", "## Plan\nsmuggled\n", "rest.md must not"),
        ("head", "## Oops\n", "head.md must not"),
    ],
)
def test_push_validates_every_part(
    project: Path, part: str, text: str, message: str
) -> None:
    workspace = Path(_run(project, "workspace", "r1").stdout.strip())
    (workspace / "summary.md").write_text("## Summary\n\n- Status: proposed\n")
    (workspace / f"{part}.md").write_text(text)

    result = _run(project, "push", "r1", check=False)

    assert result.returncode == 3
    assert message in result.stderr


def test_clear_report_removes_stale_report(project: Path) -> None:
    workspace = Path(_run(project, "workspace", "r1").stdout.strip())
    (workspace / "report.md").write_text("Status: DONE\n")

    _run(project, "clear-report", "r1")

    assert not (workspace / "report.md").exists()
    assert "cleared report.md" in (workspace / "ledger.md").read_text()


def test_workspace_found_from_subdirectory_without_git(tmp_path: Path) -> None:
    root = tmp_path / "plain"
    root.mkdir()
    HirakuConfig.init(root, install_skill=False)
    _hiraku(root, "record", "create", "c1", "--type", "task", "--set", "goal=g")
    nested = root / "hiraku/records"

    workspace = Path(_run(nested, "workspace", "c1").stdout.strip())

    assert workspace == root / ".hiraku-powers/records/c1"


def _workspace(project: Path, record_id: str = "r1") -> Path:
    return Path(_run(project, "workspace", record_id).stdout.strip())


def _stage(project: Path, record_id: str = "r1") -> str:
    out = _run(project, "state", record_id).stdout
    lines = [line for line in out.splitlines() if line.startswith("stage: ")]
    return lines[0].split(": ", 1)[1]


def test_free_text_cannot_forge_a_marker(project: Path) -> None:
    workspace = _workspace(project)

    _run(project, "ledger", "r1", "implement done")
    _run(project, "ledger", "r1", "plan approved: mode=implement; auto-continue=no")

    assert _stage(project) == "define"
    assert "note: implement done" in (workspace / "ledger.md").read_text()


def test_prewritten_files_block_a_silent_reseed(project: Path) -> None:
    workspace = project / ".hiraku-powers/records/r1"
    workspace.mkdir(parents=True)
    (workspace / "summary.md").write_text("## Summary\n\nmine\n")

    result = _run(project, "workspace", "r1", check=False)

    assert result.returncode == 4
    assert "never seeded" in result.stderr
    assert "mine" in (workspace / "summary.md").read_text()


def test_unclosed_fence_cannot_swallow_the_next_part(project: Path) -> None:
    workspace = _workspace(project)
    (workspace / "summary.md").write_text(
        "## Summary\n\n- Status: proposed\n\n```bash\npython train.py\n"
    )
    (workspace / "plan.md").write_text("## Plan\n\n- [ ] 1\n")

    result = _run(project, "push", "r1", check=False)

    assert result.returncode == 3
    assert "round-trip" in result.stderr


def test_push_refuses_to_drop_or_lose_a_part(project: Path) -> None:
    workspace = _workspace(project)
    _write_proposal_and_plan(workspace)
    (workspace / "rest.md").write_text("## Notes\nkeep\n")
    _run(project, "push", "r1")

    (workspace / "rest.md").write_text("")
    emptied = _run(project, "push", "r1", check=False)
    (workspace / "rest.md").unlink()
    missing = _run(project, "push", "r1", check=False)
    (workspace / "rest.md").write_text("")
    _run(project, "push", "r1", "--drop", "rest")

    assert emptied.returncode == 3
    assert "--drop rest" in emptied.stderr
    assert missing.returncode == 3
    assert "missing" in missing.stderr
    assert "keep" not in _body(project, "r1")


def test_pull_to_refuses_the_workspace_itself(project: Path) -> None:
    workspace = _workspace(project)
    (workspace / "summary.md").write_text("## Summary\n\n- Status: draft\n")

    result = _run(project, "pull", "r1", "--to", str(workspace), check=False)

    assert result.returncode == 2
    assert "draft" in (workspace / "summary.md").read_text()


def test_record_id_cannot_collide_with_workspace_files(project: Path) -> None:
    _hiraku(
        project, "record", "create", ".gitignore", "--type", "task", "--set", "goal=g"
    )

    workspace = _workspace(project, ".gitignore")

    assert workspace.is_dir()
    status = subprocess.run(
        ["git", "status", "--short"], cwd=project, text=True, capture_output=True
    ).stdout
    assert ".hiraku-powers" not in status


def test_ids_starting_with_dash_work(project: Path) -> None:
    _hiraku(
        project, "record", "create", "--type", "task", "--set", "goal=g", "--", "-x"
    )

    workspace = _workspace(project, "-x")

    assert workspace.name == "-x"


def test_second_writer_is_locked_out(project: Path) -> None:
    workspace = _workspace(project)
    (workspace / ".lock").mkdir()
    (workspace / "summary.md").write_text("## Summary\n\n- Status: proposed\n")

    result = _run(project, "push", "r1", check=False)

    assert result.returncode == 6
    assert "another hiraku-powers operation" in result.stderr


QUOTE = ("--quote", "yes, go ahead")
STAGES = ["define", "plan", "implement", "review", "present"]


def _write_proposal_and_plan(
    workspace: Path, mode: str = "implement-and-run", auto: str = "yes"
) -> None:
    (workspace / "summary.md").write_text(
        "## Summary\n\n- Status: proposed\n- Hypothesis: h\n- Success criterion: c\n"
    )
    (workspace / "plan.md").write_text(
        "## Plan\n\n### Execution\n"
        f"- Mode: {mode}\n- Auto-continue to review: {auto}\n\n"
        "### Tasks\n- [ ] 1. x\n\n### Deviations\n"
    )


def _advance(project: Path, stage: str, record_id: str = "r1") -> Path:
    """Drive a record through the real script sequence up to ``stage``."""
    workspace = _workspace(project, record_id)
    _write_proposal_and_plan(workspace)
    _run(project, "push", record_id)
    if stage == "define":
        return workspace
    _run(project, "approve", record_id, "proposal", *QUOTE)
    if stage == "plan":
        return workspace
    _run(project, "approve", record_id, "plan", "implement-and-run", "yes", *QUOTE)
    if stage == "implement":
        return workspace
    _run(project, "ledger", record_id, "--marker", "base")
    _run(project, "bind", record_id)
    (workspace / "report.md").write_text("Status: DONE\n")
    _run(project, "ledger", record_id, "--marker", "implement-done")
    if stage == "review":
        return workspace
    for name in ("findings.md", "result.md", "decision.md"):
        (workspace / name).write_text("supports\n")
    _run(project, "ledger", record_id, "--marker", "review-drafted")
    return workspace


def test_stage_follows_ledger_markers(project: Path) -> None:
    for stage in STAGES:
        _hiraku(
            project,
            "record",
            "create",
            f"s-{stage}",
            "--type",
            "task",
            "--set",
            "goal=g",
        )
        _advance(project, stage, f"s-{stage}")
        assert _stage(project, f"s-{stage}") == stage

    present = project / ".hiraku-powers/records/s-present"
    (present / "decision.md").write_text("adopt\n")
    _run(project, "finalize", "s-present")
    assert _stage(project, "s-present") == "done"
    assert _run(project, "inprogress").stdout.count("\n") == len(STAGES) - 1


def test_stop_then_done_flow(project: Path) -> None:
    workspace = _advance(project, "implement")
    _run(project, "ledger", "r1", "--marker", "implement-started")
    assert "dispatched: yes" in _run(project, "state", "r1").stdout
    _run(project, "ledger", "r1", "--marker", "implement-stop", "smoke failed")
    stopped = _run(project, "state", "r1").stdout
    assert "stage: implement" in stopped
    assert "stopped: smoke failed" in stopped
    assert "dispatched: no" in stopped
    _run(project, "ledger", "r1", "--marker", "base")
    _run(project, "bind", "r1")
    (workspace / "report.md").write_text("Status: DONE\n")
    _run(project, "ledger", "r1", "--marker", "implement-done")
    assert _stage(project) == "review"


@pytest.mark.parametrize(
    ("setup", "argv", "needs"),
    [
        (
            "define",
            ("approve", "r1", "plan", "implement-and-run", "yes", *QUOTE),
            "plan",
        ),
        ("plan", ("approve", "r1", "proposal", *QUOTE), "define"),
        ("plan", ("ledger", "r1", "--marker", "review-drafted"), "review"),
        ("implement", ("ledger", "r1", "--marker", "review-drafted"), "review"),
        ("implement", ("finalize", "r1"), "present"),
        ("review", ("finalize", "r1"), "present"),
        ("plan", ("bind", "r1"), "implement"),
    ],
)
def test_transitions_must_follow_the_order(
    project: Path, setup: str, argv: tuple[str, ...], needs: str
) -> None:
    workspace = _advance(project, setup)
    for name in ("result.md", "decision.md", "findings.md"):
        (workspace / name).write_text("x\n")

    result = _run(project, *argv, check=False)

    assert result.returncode == 7
    assert needs in result.stderr
    assert _stage(project) == setup


def test_implement_done_needs_bound_commit_and_done_report(project: Path) -> None:
    workspace = _advance(project, "implement")

    unbound = _run(project, "ledger", "r1", "--marker", "implement-done", check=False)
    _run(project, "bind", "r1")
    (workspace / "report.md").write_text("Status: STOP\n")
    stopped = _run(project, "ledger", "r1", "--marker", "implement-done", check=False)

    assert unbound.returncode == 3
    assert "bind" in unbound.stderr
    assert stopped.returncode == 3
    assert "Status: DONE" in stopped.stderr


def test_review_drafted_needs_all_three_drafts(project: Path) -> None:
    workspace = _advance(project, "review")
    (workspace / "findings.md").write_text("ok\n")

    result = _run(project, "ledger", "r1", "--marker", "review-drafted", check=False)

    assert result.returncode == 3


def test_approvals_need_their_preconditions(project: Path) -> None:
    workspace = _workspace(project)
    no_summary = _run(project, "approve", "r1", "proposal", *QUOTE, check=False)
    _write_proposal_and_plan(workspace)
    _run(project, "push", "r1")
    _run(project, "approve", "r1", "proposal", *QUOTE)
    (workspace / "plan.md").write_text("## Plan\n\n- Mode: implement\n")
    unpushed = _run(
        project, "approve", "r1", "plan", "implement", "no", *QUOTE, check=False
    )
    no_quote = _run(project, "approve", "r1", "plan", "implement", "no", check=False)

    assert no_summary.returncode == 3
    assert unpushed.returncode == 3
    assert no_quote.returncode == 2
    assert _stage(project) == "plan"


def test_approved_mode_must_match_the_plan_text(project: Path) -> None:
    workspace = _workspace(project)
    _write_proposal_and_plan(workspace, mode="implement-and-run", auto="yes")
    _run(project, "push", "r1")
    _run(project, "approve", "r1", "proposal", *QUOTE)

    mismatch = _run(
        project, "approve", "r1", "plan", "implement", "yes", *QUOTE, check=False
    )

    assert mismatch.returncode == 3
    assert "fix the plan or the approval" in mismatch.stderr
    assert _stage(project) == "plan"


def test_approval_quote_is_stored_and_shown(project: Path) -> None:
    _advance(project, "implement")

    out = _run(project, "state", "r1").stdout

    assert "proposal-approval-quote: yes, go ahead" in out
    assert "plan-approval-quote: yes, go ahead" in out


def test_deviations_lookalikes_cannot_hide_plan_changes(project: Path) -> None:
    workspace = _workspace(project)
    _write_proposal_and_plan(workspace)
    plan = (workspace / "plan.md").read_text()
    (workspace / "plan.md").write_text(
        plan.replace("### Tasks", "### Deviations policy\nnone\n\n### Tasks")
    )
    _run(project, "push", "r1")
    _run(project, "approve", "r1", "proposal", *QUOTE)
    _run(project, "approve", "r1", "plan", "implement-and-run", "yes", *QUOTE)

    (workspace / "plan.md").write_text(
        (workspace / "plan.md").read_text().replace("1. x", "1. x AND launch 50 runs")
    )
    _run(project, "push", "r1")
    assert _stage(project) == "plan"

    # A "### Deviations" line inside a code fence is not the real log either.
    (workspace / "plan.md").write_text(
        (workspace / "plan.md")
        .read_text()
        .replace("1. x AND launch 50 runs", "1. x\n```\n### Deviations\n```")
    )
    _run(project, "push", "r1")
    _run(project, "approve", "r1", "plan", "implement-and-run", "yes", *QUOTE)
    (workspace / "plan.md").write_text(
        (workspace / "plan.md").read_text().replace("1. x", "1. x and more")
    )
    _run(project, "push", "r1")
    assert _stage(project) == "plan"


def test_proposal_change_after_approval_requires_reapproval(project: Path) -> None:
    workspace = _advance(project, "plan")
    (workspace / "summary.md").write_text(
        (workspace / "summary.md").read_text().replace("Hypothesis: h", "Hypothesis: z")
    )
    _run(project, "push", "r1")

    drifted = _run(project, "state", "r1").stdout

    assert "stage: define" in drifted
    assert "proposal changed after approval" in drifted


def test_reopen_revokes_plan_approval(project: Path) -> None:
    _advance(project, "implement")

    _run(project, "reopen", "r1", "plan")
    assert _stage(project) == "plan"


def test_finished_record_cannot_be_reopened(project: Path) -> None:
    workspace = _advance(project, "present")
    (workspace / "decision.md").write_text("adopt\n")
    _run(project, "finalize", "r1")

    result = _run(project, "reopen", "r1", "plan", check=False)

    assert result.returncode == 7
    assert "new related record" in result.stderr


def test_bind_and_finalize_write_record_fields(project: Path) -> None:
    workspace = _advance(project, "present")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    (workspace / "result.md").write_text('supports: "yes"\n')
    (workspace / "decision.md").write_text("adopt\n")

    _run(project, "finalize", "r1")

    values = json.loads(_hiraku(project, "record", "show", "r1", "--json"))["values"]
    assert values["commit"] == head
    assert values["result"] == 'supports: "yes"'
    assert values["decision"] == "adopt"
    assert values["status"] == "reviewed"
    ledger = (workspace / "ledger.md").read_text()
    assert f"bound commit {head}" in ledger
    assert "finalized result and decision" in ledger


def test_finalize_rejects_blank_decision(project: Path) -> None:
    workspace = _advance(project, "present")
    (workspace / "result.md").write_text("supports\n")
    (workspace / "decision.md").write_text("  \n\n")

    assert _run(project, "finalize", "r1", check=False).returncode == 3


def test_bind_refuses_rebind_but_same_commit_is_a_no_op(project: Path) -> None:
    _advance(project, "implement")
    _run(project, "bind", "r1")
    again = _run(project, "bind", "r1")
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "second"],
        cwd=project,
        check=True,
    )

    refused = _run(project, "bind", "r1", check=False)
    _run(project, "bind", "r1", "--rebind")

    assert "already bound" in again.stdout
    assert refused.returncode == 5
    assert "--rebind" in refused.stderr


def test_base_is_recorded_once(project: Path) -> None:
    workspace = _advance(project, "implement")
    _run(project, "ledger", "r1", "--marker", "base")
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "later"],
        cwd=project,
        check=True,
    )

    again = _run(project, "ledger", "r1", "--marker", "base")

    assert "already recorded" in again.stdout
    assert (workspace / "ledger.md").read_text().count(" base ") == 1


def test_multiline_notes_cannot_forge_markers(project: Path) -> None:
    workspace = _advance(project, "implement")
    _run(project, "ledger", "r1", "--marker", "base")

    _run(
        project,
        "ledger",
        "r1",
        "Task 1 checks:\n- base model unchanged\n- implement done for task 1\n"
        "2000-01-01T00:00:00Z implement done",
    )
    _run(
        project, "ledger", "r1", "--marker", "implement-stop", "fail\n- review drafted"
    )

    state = _run(project, "state", "r1").stdout
    assert "stage: implement" in state
    assert "base: model" not in state
    assert len((workspace / "ledger.md").read_text().splitlines()) == len(
        [line for line in (workspace / "ledger.md").read_text().splitlines() if line]
    )


def test_hand_written_non_timestamped_lines_are_ignored(project: Path) -> None:
    workspace = _advance(project, "implement")
    with (workspace / "ledger.md").open("a") as ledger:
        ledger.write("implement done\nfinalized\n")

    assert _stage(project) == "implement"


def test_stale_lock_from_a_dead_process_is_replaced(project: Path) -> None:
    workspace = _workspace(project)
    (workspace / ".lock").mkdir()
    (workspace / ".lock/pid").write_text("999999\n")
    (workspace / "summary.md").write_text("## Summary\n\n- Status: proposed\n")

    _run(project, "push", "r1")

    assert not (workspace / ".lock").exists()


def test_push_recovers_after_a_crash_between_write_and_snapshot(
    project: Path,
) -> None:
    workspace = _workspace(project)
    (workspace / "summary.md").write_text("## Summary\n\n- Status: proposed\n")
    _run(project, "push", "r1")
    # Simulate a push that wrote the record but died before re-snapshotting.
    (workspace / ".pulled-sha").write_text("0" * 64 + "\n")

    result = _run(project, "push", "r1", check=False)

    assert result.returncode == 0


def test_head_text_triggers_a_warning(project: Path) -> None:
    workspace = _workspace(project)
    (workspace / "head.md").write_text("# Title\n")
    (workspace / "summary.md").write_text("## Summary\n\n- Status: proposed\n")

    result = _run(project, "push", "r1")

    assert "does not start with" in result.stderr or "will not start" in result.stderr


def test_plan_change_after_approval_requires_reapproval(project: Path) -> None:
    workspace = _advance(project, "implement")

    # Ticks and deviations are progress, not plan changes.
    plan = (workspace / "plan.md").read_text()
    (workspace / "plan.md").write_text(
        plan.replace("- [ ] 1.", "- [x] 1.") + "- smoke slow\n"
    )
    _run(project, "push", "r1")
    assert _stage(project) == "implement"

    (workspace / "plan.md").write_text(
        (workspace / "plan.md")
        .read_text()
        .replace("### Tasks", "### Tasks\n- [ ] 2. new work")
    )
    _run(project, "push", "r1")
    drifted = _run(project, "state", "r1").stdout
    assert "stage: plan" in drifted
    assert "plan changed after approval" in drifted
    # The log from earlier progress may stay on a re-approval.
    _run(project, "approve", "r1", "plan", "implement-and-run", "yes", *QUOTE)
    assert _stage(project) == "implement"


def test_tick_styles_and_duplicate_deviation_headings_are_progress(
    project: Path,
) -> None:
    workspace = _workspace(project)
    _write_proposal_and_plan(workspace)
    plan = (workspace / "plan.md").read_text()
    (workspace / "plan.md").write_text(
        plan.replace("- [ ] 1. x\n", "- [ ] 1. x\n* [ ] 2. y\n1. [ ] 3. z\n")
    )
    _run(project, "push", "r1")
    _run(project, "approve", "r1", "proposal", *QUOTE)
    _run(project, "approve", "r1", "plan", "implement-and-run", "yes", *QUOTE)

    ticked = (workspace / "plan.md").read_text()
    ticked = ticked.replace("* [ ]", "* [x]").replace("1. [ ]", "1. [x]")
    (workspace / "plan.md").write_text(
        ticked + "\n### Deviations\n- an honest second heading\n"
    )
    _run(project, "push", "r1")

    assert _stage(project) == "implement"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.replace("### Deviations\n", "### Deviations\n- planned work\n"),
        lambda p: p + "\n### Evidence\n- threshold +1.0\n",
        lambda p: p.replace("### Deviations\n", "") + "### Notes\n",
    ],
)
def test_approving_a_plan_requires_a_last_and_empty_deviations_log(
    project: Path, mutate
) -> None:
    workspace = _workspace(project)
    _write_proposal_and_plan(workspace)
    (workspace / "plan.md").write_text(mutate((workspace / "plan.md").read_text()))
    _run(project, "push", "r1")
    _run(project, "approve", "r1", "proposal", *QUOTE)

    result = _run(
        project,
        "approve",
        "r1",
        "plan",
        "implement-and-run",
        "yes",
        *QUOTE,
        check=False,
    )

    assert result.returncode == 3
    assert "Deviations" in result.stderr
    assert _stage(project) == "plan"


@pytest.mark.parametrize(
    "quote",
    [
        "ok, mode=implement-and-run; auto-continue=yes; fine",
        "go plan-sha=" + "a" * 64 + " | quote: x",
        "proposal approved: summary-sha=" + "b" * 64 + " | quote: y",
        "multi\nline; mode=implement-and-run",
    ],
)
def test_approval_quotes_cannot_override_marker_fields(
    project: Path, quote: str
) -> None:
    workspace = _workspace(project)
    _write_proposal_and_plan(workspace, mode="implement", auto="no")
    _run(project, "push", "r1")
    _run(project, "approve", "r1", "proposal", "--quote", quote)
    _run(project, "approve", "r1", "plan", "implement", "no", "--quote", quote)

    state = _run(project, "state", "r1").stdout

    assert "stage: implement" in state
    assert "mode: implement\n" in state
    assert "auto-continue: no\n" in state


def test_reopen_plan_cannot_skip_the_proposal_gate(project: Path) -> None:
    workspace = _workspace(project)
    _write_proposal_and_plan(workspace)
    _run(project, "push", "r1")

    refused = _run(project, "reopen", "r1", "plan", check=False)
    # Even a hand-written marker only lowers a stage, never raises one.
    with (workspace / "ledger.md").open("a") as ledger:
        ledger.write("2026-01-01T00:00:00Z plan reopened\n")
    approve = _run(
        project,
        "approve",
        "r1",
        "plan",
        "implement-and-run",
        "yes",
        *QUOTE,
        check=False,
    )

    assert refused.returncode == 7
    assert approve.returncode == 7
    assert _stage(project) == "define"


def test_exit_seven_explains_a_drifted_plan_and_stop_stays_possible(
    project: Path,
) -> None:
    workspace = _advance(project, "implement")
    (workspace / "plan.md").write_text(
        (workspace / "plan.md").read_text().replace("1. x", "1. x and y")
    )
    _run(project, "push", "r1")

    refused = _run(project, "ledger", "r1", "--marker", "base", check=False)
    stop = _run(project, "ledger", "r1", "--marker", "implement-stop", "plan drifted")

    assert refused.returncode == 7
    assert "plan changed after approval" in refused.stderr
    assert stop.returncode == 0


def test_main_agent_can_close_a_dispatch_that_left_no_marker(project: Path) -> None:
    _advance(project, "implement")
    _run(project, "ledger", "r1", "--marker", "implement-started")
    assert "dispatched: yes" in _run(project, "state", "r1").stdout

    _run(project, "ledger", "r1", "--marker", "implement-stop", "subagent timed out")
    closed = _run(project, "state", "r1").stdout
    _run(project, "ledger", "r1", "--marker", "implement-started")
    redispatched = _run(project, "state", "r1").stdout

    assert "dispatched: no" in closed
    assert "stopped: subagent timed out" in closed
    assert "dispatched: yes" in redispatched
    assert "stopped:" not in redispatched


def test_proposal_pin_covers_whitespace_but_not_progress_lines(
    project: Path,
) -> None:
    workspace = _advance(project, "implement")
    summary = (workspace / "summary.md").read_text()

    (workspace / "summary.md").write_text(
        summary.replace("Status: proposed", "Status: implemented") + "- Evidence: x\n"
    )
    _run(project, "push", "r1")
    assert _stage(project) == "implement"

    (workspace / "summary.md").write_text(
        (workspace / "summary.md")
        .read_text()
        .replace("Hypothesis: h", "Hypothesis:  h")
    )
    _run(project, "push", "r1")
    assert _stage(project) == "define"


def test_recreated_record_does_not_inherit_old_workspace(project: Path) -> None:
    workspace = _advance(project, "plan")
    _hiraku(project, "record", "delete", "r1")
    _hiraku(project, "record", "create", "r1", "--type", "task", "--set", "goal=g")

    fresh = _workspace(project)

    assert fresh == workspace
    assert _stage(project) == "define"
    assert list((workspace.parents[1] / "archive").glob("r1-*"))
    assert "r1\tdefine" in _run(project, "inprogress").stdout


def test_listing_never_archives_a_workspace_silently(project: Path) -> None:
    workspace = _advance(project, "implement")
    _hiraku(project, "record", "update", "r1", "--markdown-file", str(_empty(project)))

    listing = _run(project, "inprogress")

    assert "empty but its workspace has content" in listing.stderr
    assert (workspace / "ledger.md").is_file()
    assert not (workspace.parents[1] / "archive").exists()


def _empty(project: Path) -> Path:
    path = project / "empty.md"
    path.write_text("")
    return path


def test_drop_is_validated(project: Path) -> None:
    workspace = _workspace(project)
    (workspace / "summary.md").write_text("## Summary\n\n- Status: proposed\n")

    assert _run(project, "push", "r1", "--drop", "summary", check=False).returncode == 2
    assert _run(project, "push", "r1", "--drop", "bogus", check=False).returncode == 2


def test_sections_awk_is_portable_across_awk_flavours() -> None:
    body = "# T\n\n## Notes\n" + FENCED + "\n## Summary\nreal\n"
    plan = (
        "## Plan\n\n- [x] a\n* [X] b\n```\n- [x] code\n### Deviations\n```\n"
        "### Tasks\n1. [x] c\n\n### Deviations\n- late\n### Deviations\n- later\n"
    )
    awk = SCRIPTS / "_sections.awk"
    flavours = [
        cmd
        for cmd in (["gawk"], ["mawk"], ["busybox", "awk"], ["awk"])
        if shutil.which(cmd[0])
    ]
    assert len(flavours) >= 2

    def run(cmd: list[str], text: str, *flags: str) -> str:
        return subprocess.run(
            [*cmd, *flags, "-f", str(awk)],
            input=text,
            text=True,
            capture_output=True,
            check=True,
        ).stdout

    seen = {
        (
            run(cmd, body, "-v", "mode=split", "-v", "want=Summary"),
            run(cmd, body, "-v", "mode=headings"),
            run(cmd, plan, "-v", "mode=planbody"),
            run(cmd, plan, "-v", "mode=h3"),
            run(cmd, plan, "-v", "mode=devtail"),
        )
        for cmd in flavours
    }
    assert len(seen) == 1
    ((split, headings, planbody, h3, devtail),) = seen
    assert split == "## Summary\nreal\n"
    assert headings == "## Notes\n## Summary\n"
    # Ticks normalised everywhere outside fences, cut at the FIRST real heading.
    assert planbody == (
        "## Plan\n\n- [ ] a\n* [ ] b\n```\n- [x] code\n### Deviations\n```\n"
        "### Tasks\n1. [ ] c\n\n"
    )
    assert h3 == "### Tasks\n### Deviations\n### Deviations\n"
    assert devtail == "- late\n### Deviations\n- later\n"


def test_implement_stop_cannot_resurrect_a_reopened_stage(project: Path) -> None:
    _advance(project, "implement")
    _run(project, "reopen", "r1", "plan")

    _run(project, "ledger", "r1", "--marker", "implement-stop", "subagent crashed")
    after_plan = _run(project, "state", "r1").stdout

    _run(project, "reopen", "r1", "proposal")
    _run(project, "approve", "r1", "proposal", *QUOTE)
    _run(project, "ledger", "r1", "--marker", "implement-stop", "again")
    after_proposal = _run(project, "state", "r1").stdout

    assert "stage: plan" in after_plan
    assert "stage: plan" in after_proposal
    refused = _run(project, "ledger", "r1", "--marker", "base", check=False)
    assert refused.returncode == 7


def test_plan_template_passes_its_own_approval_rules() -> None:
    path = SCRIPTS.parent / "plan/plan-template.md"
    text = path.read_text()
    block = text.split("```markdown\n", 1)[1].split("\n```", 1)[0]
    awk = SCRIPTS / "_sections.awk"

    def run(mode: str) -> str:
        return subprocess.run(
            ["awk", "-v", f"mode={mode}", "-f", str(awk)],
            input=block,
            text=True,
            capture_output=True,
            check=True,
        ).stdout

    assert run("devtail").strip() == ""
    assert run("h3").splitlines()[-1] == "### Deviations"
    assert run("h3").count("### Deviations") == 1
    assert block.count("- [ ] 2.") == 1


def test_state_never_archives_but_a_direct_workspace_call_does(
    project: Path,
) -> None:
    workspace = _advance(project, "implement")
    _hiraku(project, "record", "update", "r1", "--markdown-file", str(_empty(project)))

    state = _run(project, "state", "r1")

    assert "empty but its workspace has content" in state.stderr
    assert "stage: define" in state.stdout
    assert "proposal changed after approval" in state.stdout
    assert (workspace / "ledger.md").is_file()
    assert not (workspace.parents[1] / "archive").exists()
    _run(project, "workspace", "r1")
    assert list((workspace.parents[1] / "archive").glob("r1-*"))


def test_state_shows_only_approvals_still_in_force(project: Path) -> None:
    _advance(project, "implement")
    _run(project, "reopen", "r1", "proposal")

    out = _run(project, "state", "r1").stdout

    assert "stage: define" in out
    assert "mode: implement" not in out
    assert "approval-quote" not in out


def test_invalid_utf8_cannot_hide_a_marker(project: Path) -> None:
    workspace = _workspace(project)
    _write_proposal_and_plan(workspace)
    _run(project, "push", "r1")

    _run(project, "approve", "r1", "proposal", "--quote", b"ja \xff\xfe ok")
    _run(project, "ledger", "r1", b"latin1 \xe9 note")

    assert _stage(project) == "plan"


def test_malformed_markers_are_reported_not_silently_ignored(project: Path) -> None:
    workspace = _workspace(project)
    with (workspace / "ledger.md").open("a") as ledger:
        ledger.write("2026-01-01T00:00:00Z proposal approved | quote: x\n")

    out = _run(project, "state", "r1").stdout

    assert "stage: define" in out
    assert "warning: ignored malformed marker" in out


def test_push_explains_why_summary_cannot_be_dropped(project: Path) -> None:
    workspace = _workspace(project)
    (workspace / "summary.md").write_text("## Summary\n\n- Status: proposed\n")
    _run(project, "push", "r1")
    (workspace / "summary.md").write_text("")

    result = _run(project, "push", "r1", check=False)

    assert result.returncode == 3
    assert "cannot be dropped" in result.stderr
    assert "--drop summary" not in result.stderr


def test_plan_with_two_mode_lines_is_not_approved(project: Path) -> None:
    workspace = _workspace(project)
    _write_proposal_and_plan(workspace, mode="implement", auto="no")
    plan = (workspace / "plan.md").read_text()
    (workspace / "plan.md").write_text(
        plan.replace(
            "- Mode: implement\n", "- Mode: implement\n- Mode: implement-and-run\n"
        )
    )
    _run(project, "push", "r1")
    _run(project, "approve", "r1", "proposal", *QUOTE)

    result = _run(
        project, "approve", "r1", "plan", "implement", "no", *QUOTE, check=False
    )

    assert result.returncode == 3
    assert "exactly one '- Mode:'" in result.stderr


def test_review_dispatch_is_tracked_and_reopen_waits_for_subagents(
    project: Path,
) -> None:
    _advance(project, "review")
    _run(project, "ledger", "r1", "--marker", "review-started")
    started = _run(project, "state", "r1").stdout
    refused = _run(project, "reopen", "r1", "plan", check=False)

    _run(project, "ledger", "r1", "--marker", "review-stop", "reviewer died")
    stopped = _run(project, "state", "r1").stdout

    assert "dispatched: yes" in started
    assert refused.returncode == 7
    assert "still dispatched" in refused.stderr
    assert "dispatched: no" in stopped
    _run(project, "reopen", "r1", "plan")
    assert _stage(project) == "plan"


def test_reordered_body_is_reported_when_push_recovers(project: Path) -> None:
    workspace = _workspace(project)
    (workspace / "summary.md").write_text("## Summary\n\n- Status: proposed\n")
    (workspace / "plan.md").write_text("## Plan\n\n- [ ] 1\n")
    _run(project, "push", "r1")
    reordered = project / "reordered.md"
    reordered.write_text("## Plan\n\n- [ ] 1\n\n## Summary\n\n- Status: proposed\n")
    _hiraku(project, "record", "update", "r1", "--markdown-file", str(reordered))

    result = _run(project, "push", "r1")

    assert "matches the workspace parts" in result.stderr
    assert _body(project, "r1").startswith("## Summary")
