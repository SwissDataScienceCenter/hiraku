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
QUOTE = ("--quote", "yes, go ahead")
PLAN = """\
## Plan

### Execution
- Mode: implement-and-run
- Auto-continue to review: yes

### Tasks
- [ ] 1. change - files: a.py - check: true

### Deviations
"""

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


def _hiraku(project: Path, *args: str) -> str:
    return subprocess.run(
        ["hiraku", *args],
        cwd=project,
        env=_env(),
        text=True,
        capture_output=True,
        check=True,
    ).stdout


def _values(project: Path, record_id: str = "r1") -> dict:
    return json.loads(_hiraku(project, "record", "show", record_id, "--raw", "--json"))


def _stage(project: Path, record_id: str = "r1") -> str:
    out = _run(project, "state", record_id).stdout
    return next(line for line in out.splitlines() if line.startswith("stage: "))[7:]


def _write_body(project: Path, text: str, record_id: str = "r1") -> Path:
    workspace = Path(_run(project, "open", record_id).stdout.strip())
    (workspace / "body.md").write_text(text)
    _run(project, "save", record_id)
    return workspace


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
    _hiraku(root, "record", "create", "root", "--type", "task", "--set", "goal=g")
    _create(root, "r1")
    return root


def _create(project: Path, record_id: str) -> None:
    _hiraku(
        project,
        "record",
        "create",
        record_id,
        "--parent",
        "root",
        "--no-activate",
        "--set",
        "hypothesis=h",
        "--set",
        "success_criterion=c",
        "--set",
        "status=proposed",
    )


STAGES = ("define", "plan", "implement", "review", "present")


def _advance(project: Path, stage: str, record_id: str = "r1") -> Path:
    """Drive a record through the scripts up to the given stage."""
    workspace = Path(_run(project, "open", record_id).stdout.strip())
    if stage == "define":
        return workspace
    _run(project, "approve", record_id, "proposal", *QUOTE)
    if stage == "plan":
        return workspace
    _write_body(project, PLAN, record_id)
    _run(project, "approve", record_id, "plan", *QUOTE)
    if stage == "implement":
        return workspace
    _run(project, "ledger", record_id, "--marker", "implement-started")
    _run(project, "ledger", record_id, "--marker", "base")
    _run(project, "bind", record_id)
    (workspace / "implement-notes.md").write_text("Status: DONE\n")
    _run(project, "ledger", record_id, "--marker", "implement-done")
    if stage == "review":
        return workspace
    _run(project, "ledger", record_id, "--marker", "review-started")
    for name in ("review-notes.md", "result.md", "decision.md"):
        (workspace / name).write_text("supports: x\n")
    _run(project, "ledger", record_id, "--marker", "review-drafted")
    return workspace


def test_open_creates_an_ignored_workspace_and_protects_unsaved_edits(
    project: Path,
) -> None:
    _hiraku(project, "record", "update", "r1", "--markdown-file", "/dev/null")
    workspace = Path(_run(project, "open", "r1").stdout.strip())

    assert workspace == project / ".hiraku-powers/records/r1"
    assert (workspace / "ledger.md").is_file()
    status = subprocess.run(
        ["git", "status", "--short"], cwd=project, text=True, capture_output=True
    ).stdout
    assert ".hiraku-powers" not in status

    (workspace / "body.md").write_text("## Notes\nunsaved\n")
    refused = _run(project, "open", "r1", check=False)
    assert refused.returncode == 4
    assert "unsaved" in (workspace / "body.md").read_text()
    _run(project, "open", "r1", "--discard")
    assert "unsaved" not in (workspace / "body.md").read_text()


def test_save_writes_the_body_and_refuses_a_stale_one(project: Path) -> None:
    workspace = _write_body(project, "## Notes\nmine\n")
    assert "mine" in _values(project)["markdown"]

    other = project / "other.md"
    other.write_text("## Notes\nedited elsewhere\n")
    _hiraku(project, "record", "update", "r1", "--markdown-file", str(other))
    (workspace / "body.md").write_text("## Notes\nmine again\n")

    stale = _run(project, "save", "r1", check=False)
    assert stale.returncode == 4
    assert "edited elsewhere" in _values(project)["markdown"]
    _run(project, "save", "r1", "--force")
    assert "mine again" in _values(project)["markdown"]


def test_save_refuses_two_plans_but_ignores_fenced_headings(project: Path) -> None:
    workspace = Path(_run(project, "open", "r1").stdout.strip())
    (workspace / "body.md").write_text("## Plan\na\n\n## Plan\nb\n")
    assert _run(project, "save", "r1", check=False).returncode == 3

    (workspace / "body.md").write_text("## Plan\na\n\n```\n## Plan\n```\n")
    _run(project, "save", "r1")


def test_state_is_read_only(project: Path) -> None:
    assert _stage(project) == "define"
    assert not (project / ".hiraku-powers").exists()


def test_full_cycle_sets_status_and_fields(project: Path) -> None:
    for stage in STAGES:
        if stage != "define":
            _create(project, f"s-{stage}")
        _advance(project, stage, "r1" if stage == "define" else f"s-{stage}")
        assert _stage(project, "r1" if stage == "define" else f"s-{stage}") == stage

    assert _values(project, "s-plan")["values"]["status"] == "proposed"
    assert _values(project, "s-implement")["values"]["status"] == "planned"
    assert _values(project, "s-review")["values"]["status"] == "implemented"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=project, text=True, capture_output=True
    ).stdout.strip()
    assert _values(project, "s-review")["values"]["commit"] == head

    _run(project, "finalize", "s-present")
    values = _values(project, "s-present")["values"]
    assert _stage(project, "s-present") == "done"
    assert values["status"] == "reviewed"
    assert values["result"] == "supports: x"
    listed = _run(project, "inprogress").stdout
    assert "s-present" not in listed
    assert "s-review\treview" in listed


def test_inprogress_says_when_nothing_is_open(project: Path) -> None:
    assert _run(project, "inprogress").stdout.strip() == "no unfinished records"


def test_approve_proposal_needs_both_fields(project: Path) -> None:
    _hiraku(project, "record", "update", "r1", "--unset", "success_criterion")

    result = _run(project, "approve", "r1", "proposal", *QUOTE, check=False)

    assert result.returncode == 3
    assert "success_criterion" in result.stderr


def test_approve_needs_a_quote(project: Path) -> None:
    result = _run(project, "approve", "r1", "proposal", "--quote", " ", check=False)
    assert result.returncode == 2


def test_approve_plan_reads_mode_and_auto_continue_from_the_plan(
    project: Path,
) -> None:
    _advance(project, "plan")
    _write_body(project, PLAN.replace("implement-and-run", "implement"))

    out = _run(project, "approve", "r1", "plan", *QUOTE).stdout

    assert "mode implement, auto-continue yes" in out
    state = _run(project, "state", "r1").stdout
    assert "mode: implement\n" in state
    assert "plan-approval-quote: yes, go ahead" in state


@pytest.mark.parametrize(
    ("plan", "message"),
    [
        (PLAN.replace("implement-and-run", "maybe"), "must be implement"),
        (PLAN.replace("- Auto-continue to review: yes", ""), "exactly one"),
        (PLAN + "- Mode: implement\n", "exactly one"),
        (PLAN.replace("### Deviations\n", ""), "### Deviations"),
        (PLAN + "### Later\n", "last ### heading"),
        ("## Notes\nno plan\n", "no '## Plan'"),
        (PLAN.replace("### Tasks", "- Implement: maybe\n\n### Tasks"), "subagent or"),
        (
            PLAN.replace(
                "### Tasks", "- Implement: inline\n- Implement: inline\n\n### Tasks"
            ),
            "more than one",
        ),
    ],
)
def test_approve_plan_rejects_malformed_plans(
    project: Path, plan: str, message: str
) -> None:
    _advance(project, "plan")
    _write_body(project, plan)

    result = _run(project, "approve", "r1", "plan", *QUOTE, check=False)

    assert result.returncode == 3
    assert message in result.stderr
    assert _stage(project) == "plan"


def test_approve_plan_refuses_unsaved_edits(project: Path) -> None:
    workspace = _advance(project, "plan")
    (workspace / "body.md").write_text(PLAN)

    result = _run(project, "approve", "r1", "plan", *QUOTE, check=False)

    assert result.returncode == 3
    assert "save" in result.stderr


def test_fast_path_approves_proposal_and_plan_at_once(project: Path) -> None:
    _write_body(project, PLAN)

    _run(project, "approve", "r1", "proposal-and-plan", *QUOTE)

    assert _stage(project) == "implement"


def test_ticks_and_deviations_are_progress_not_plan_changes(project: Path) -> None:
    workspace = _advance(project, "implement")
    progress = PLAN.replace("- [ ] 1.", "- [x] 1.") + "- used a different path\n"
    _write_body(project, progress)
    assert _stage(project) == "implement"

    (workspace / "body.md").write_text(progress.replace("change", "other change"))
    _run(project, "save", "r1")
    state = _run(project, "state", "r1").stdout
    assert "stage: plan" in state
    assert "plan changed after approval" in state


def test_fenced_deviations_heading_does_not_hide_plan_changes(project: Path) -> None:
    fenced = PLAN.replace("### Tasks", "```\n### Deviations\n```\n\n### Tasks")
    _advance(project, "plan")
    _write_body(project, fenced)
    _run(project, "approve", "r1", "plan", *QUOTE)

    _write_body(project, fenced.replace("change", "other change"))

    assert _stage(project) == "plan"


def test_hypothesis_change_after_approval_returns_to_define(project: Path) -> None:
    _advance(project, "implement")

    _hiraku(project, "record", "update", "r1", "--set", "hypothesis=other")

    state = _run(project, "state", "r1").stdout
    assert "stage: define" in state
    assert "hypothesis or success criterion changed" in state


@pytest.mark.parametrize(
    ("setup", "argv", "needs"),
    [
        ("define", ("approve", "r1", "plan", *QUOTE), "plan"),
        ("plan", ("approve", "r1", "proposal", *QUOTE), "define"),
        ("plan", ("bind", "r1"), "implement"),
        ("implement", ("ledger", "r1", "--marker", "review-drafted"), "review"),
        ("review", ("finalize", "r1"), "present"),
    ],
)
def test_out_of_order_steps_exit_seven(
    project: Path, setup: str, argv: tuple[str, ...], needs: str
) -> None:
    _advance(project, setup)

    result = _run(project, *argv, check=False)

    assert result.returncode == 7
    assert f"this step needs: {needs}" in result.stderr


def test_implement_done_needs_bound_commit_and_done_notes(project: Path) -> None:
    workspace = _advance(project, "implement")
    (workspace / "implement-notes.md").write_text("Status: DONE\n")
    _run(project, "ledger", "r1", "--marker", "implement-started")
    assert not (workspace / "implement-notes.md").exists()

    no_commit = _run(project, "ledger", "r1", "--marker", "implement-done", check=False)
    assert "bind it first" in no_commit.stderr
    _run(project, "bind", "r1")
    (workspace / "implement-notes.md").write_text("Status: STOP\n")
    stopped = _run(project, "ledger", "r1", "--marker", "implement-done", check=False)
    assert "Status: DONE" in stopped.stderr
    assert _stage(project) == "implement"


def test_stop_and_redispatch(project: Path) -> None:
    _advance(project, "implement")
    _run(project, "ledger", "r1", "--marker", "implement-started")
    assert "dispatched: yes" in _run(project, "state", "r1").stdout
    assert _run(project, "reopen", "r1", "plan", check=False).returncode == 7

    _run(project, "ledger", "r1", "--marker", "implement-stop", "smoke failed")

    state = _run(project, "state", "r1").stdout
    assert "stopped: smoke failed" in state
    assert "dispatched: no" in state
    _run(project, "ledger", "r1", "--marker", "implement-started")
    assert "stopped:" not in _run(project, "state", "r1").stdout


def test_review_drafted_needs_all_three_drafts(project: Path) -> None:
    workspace = _advance(project, "review")
    (workspace / "result.md").write_text("supports: x\n")

    result = _run(project, "ledger", "r1", "--marker", "review-drafted", check=False)

    assert result.returncode == 3
    assert "review-notes.md" in result.stderr


def test_bind_refuses_a_rebind_but_same_commit_is_a_no_op(project: Path) -> None:
    _advance(project, "implement")
    _run(project, "bind", "r1")
    assert "already bound" in _run(project, "bind", "r1").stdout
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "b"], cwd=project)

    assert _run(project, "bind", "r1", check=False).returncode == 5
    _run(project, "bind", "r1", "--rebind")
    assert "replaced" in (project / ".hiraku-powers/records/r1/ledger.md").read_text()


def test_base_is_recorded_once(project: Path) -> None:
    _advance(project, "implement")
    first = _run(project, "ledger", "r1", "--marker", "base").stdout
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "b"], cwd=project)

    again = _run(project, "ledger", "r1", "--marker", "base").stdout

    assert "already recorded" in again
    assert first.split()[-1] in again


def test_notes_cannot_forge_markers(project: Path) -> None:
    _advance(project, "plan")

    _run(project, "ledger", "r1", "x\n2026-01-01T00:00:00Z plan approved: mode=a")

    assert _stage(project) == "plan"


def test_reopen_rules(project: Path) -> None:
    _advance(project, "implement")
    _run(project, "reopen", "r1", "plan")
    assert _stage(project) == "plan"
    _run(project, "approve", "r1", "plan", *QUOTE)

    _run(project, "reopen", "r1", "proposal")
    assert _stage(project) == "define"
    # A later plan approval cannot skip the reopened proposal.
    ledger = project / ".hiraku-powers/records/r1/ledger.md"
    approved = [
        line for line in ledger.read_text().splitlines() if "plan approved" in line
    ]
    ledger.write_text(ledger.read_text() + approved[-1] + "\n")
    assert _stage(project) == "define"

    _create(project, "done")
    _advance(project, "present", "done")
    _run(project, "finalize", "done")
    assert _run(project, "reopen", "done", "plan", check=False).returncode == 7


def test_state_shows_the_effective_branch(project: Path) -> None:
    assert "branch: (none set" in _run(project, "state", "r1").stdout

    _hiraku(project, "record", "update", "root", "--set", "branch=exp")

    assert "branch: exp\n" in _run(project, "state", "r1").stdout


def test_sections_awk_agrees_across_awk_flavours() -> None:
    body = "## Notes\n```\n## Plan\n```\n## Plan\nreal\n## Other\nx\n"
    plan = (
        "## Plan\n\n- [x] a\n```\n- [x] code\n### Deviations\n```\n"
        "### Deviations\n- late\n"
    )
    flavours = [
        cmd
        for cmd in (["gawk"], ["mawk"], ["busybox", "awk"], ["awk"])
        if shutil.which(cmd[0])
    ]

    def run(cmd: list[str], text: str, mode: str) -> str:
        return subprocess.run(
            [*cmd, "-v", f"mode={mode}", "-f", str(SCRIPTS / "_sections.awk")],
            input=text,
            text=True,
            capture_output=True,
            check=True,
        ).stdout

    seen = {
        (run(cmd, body, "h2"), run(cmd, body, "plan"), run(cmd, plan, "planhash"))
        for cmd in flavours
    }
    assert len(seen) == 1
    ((h2, section, planhash),) = seen
    assert h2 == "## Notes\n## Plan\n## Other\n"
    assert section == "## Plan\nreal\n"
    assert planhash == "## Plan\n\n- [ ] a\n```\n- [x] code\n### Deviations\n```\n"


def test_plan_template_passes_its_own_approval_rules(project: Path) -> None:
    text = (SCRIPTS.parent / "plan/plan-template.md").read_text()
    block = text.split("```markdown\n", 1)[1].split("\n```", 1)[0] + "\n"
    _advance(project, "plan")
    _write_body(project, block)

    _run(project, "approve", "r1", "plan", *QUOTE)

    assert _stage(project) == "implement"


def test_small_cycle_is_approved_once_and_shown_by_state(project: Path) -> None:
    _write_body(project, PLAN.replace("### Tasks", "- Implement: inline\n\n### Tasks"))

    out = _run(project, "approve", "r1", "proposal-and-plan", *QUOTE).stdout

    assert "implement inline" in out
    assert "implement: inline\n" in _run(project, "state", "r1").stdout


def test_implement_defaults_to_subagent(project: Path) -> None:
    assert "implement: \n" in _run(project, "state", "r1").stdout
    _advance(project, "implement")

    assert "implement: subagent\n" in _run(project, "state", "r1").stdout
