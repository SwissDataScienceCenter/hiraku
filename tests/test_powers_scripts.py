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


def test_bind_and_finalize_write_record_fields(project: Path) -> None:
    workspace = Path(_run(project, "workspace", "r1").stdout.strip())
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()

    _run(project, "bind", "r1")
    missing = _run(project, "finalize", "r1", check=False)
    (workspace / "result.md").write_text('supports: "yes"\n')
    (workspace / "decision.md").write_text("adopt\n")
    _run(project, "finalize", "r1")

    values = json.loads(_hiraku(project, "record", "show", "r1", "--json"))["values"]
    assert values["commit"] == head
    assert values["result"] == 'supports: "yes"'
    assert values["decision"] == "adopt"
    assert values["status"] == "reviewed"
    assert missing.returncode == 3
    ledger = (workspace / "ledger.md").read_text()
    assert f"bound commit {head}" in ledger
    assert "finalized result and decision" in ledger


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


def test_bind_refuses_rebind_without_flag(project: Path) -> None:
    _run(project, "bind", "r1")
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "second"],
        cwd=project,
        check=True,
    )

    refused = _run(project, "bind", "r1", check=False)
    _run(project, "bind", "r1", "--rebind")

    assert refused.returncode == 5
    assert "--rebind" in refused.stderr


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


def _write_proposal_and_plan(workspace: Path) -> None:
    (workspace / "summary.md").write_text(
        "## Summary\n\n- Status: proposed\n- Hypothesis: h\n- Success criterion: c\n"
    )
    (workspace / "plan.md").write_text(
        "## Plan\n\n### Tasks\n- [ ] 1. x\n\n### Deviations\n"
    )


def test_stage_follows_ledger_markers(project: Path) -> None:
    workspace = _workspace(project)
    assert _stage(project) == "define"
    _write_proposal_and_plan(workspace)
    _run(project, "push", "r1")

    _run(project, "approve", "r1", "proposal")
    assert _stage(project) == "plan"
    _run(project, "approve", "r1", "plan", "implement-and-run", "yes")
    assert _stage(project) == "implement"
    _run(project, "ledger", "r1", "--marker", "base")
    _run(project, "ledger", "r1", "--marker", "implement-stop", "smoke failed")
    stopped = _run(project, "state", "r1").stdout
    assert "stage: implement" in stopped
    assert "stopped: smoke failed" in stopped
    _run(project, "ledger", "r1", "--marker", "implement-done")
    assert _stage(project) == "review"
    _run(project, "ledger", "r1", "--marker", "review-drafted")
    assert _stage(project) == "present"
    (workspace / "result.md").write_text("supports\n")
    (workspace / "decision.md").write_text("adopt\n")
    _run(project, "finalize", "r1")
    assert _stage(project) == "done"
    assert _run(project, "inprogress").stdout == ""


def test_approvals_need_their_preconditions(project: Path) -> None:
    workspace = _workspace(project)
    no_summary = _run(project, "approve", "r1", "proposal", check=False)
    _write_proposal_and_plan(workspace)
    unpushed = _run(project, "approve", "r1", "plan", "implement", "no", check=False)

    assert no_summary.returncode == 3
    assert unpushed.returncode == 3
    assert _stage(project) == "define"


def test_plan_change_after_approval_requires_reapproval(project: Path) -> None:
    workspace = _workspace(project)
    _write_proposal_and_plan(workspace)
    _run(project, "push", "r1")
    _run(project, "approve", "r1", "proposal")
    _run(project, "approve", "r1", "plan", "implement", "no")

    # Ticks and deviations are progress, not plan changes.
    (workspace / "plan.md").write_text(
        "## Plan\n\n### Tasks\n- [x] 1. x\n\n### Deviations\n- smoke slow\n"
    )
    _run(project, "push", "r1")
    assert _stage(project) == "implement"

    (workspace / "plan.md").write_text(
        "## Plan\n\n### Tasks\n- [x] 1. x\n- [ ] 2. new work\n\n### Deviations\n"
    )
    _run(project, "push", "r1")
    drifted = _run(project, "state", "r1").stdout
    assert "stage: plan" in drifted
    assert "plan changed after approval" in drifted
    _run(project, "approve", "r1", "plan", "implement", "no")
    assert _stage(project) == "implement"


def test_reopen_revokes_plan_approval(project: Path) -> None:
    workspace = _workspace(project)
    _write_proposal_and_plan(workspace)
    _run(project, "push", "r1")
    _run(project, "approve", "r1", "proposal")
    _run(project, "approve", "r1", "plan", "implement", "no")

    _run(project, "reopen", "r1", "plan")

    assert _stage(project) == "plan"


def test_free_text_cannot_forge_a_marker(project: Path) -> None:
    workspace = _workspace(project)

    _run(project, "ledger", "r1", "implement done")
    _run(project, "ledger", "r1", "plan approved: mode=implement; auto-continue=no")

    assert _stage(project) == "define"
    assert "note: implement done" in (workspace / "ledger.md").read_text()


def test_base_is_recorded_once(project: Path) -> None:
    workspace = _workspace(project)
    _run(project, "ledger", "r1", "--marker", "base")
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "later"],
        cwd=project,
        check=True,
    )

    again = _run(project, "ledger", "r1", "--marker", "base")

    assert "already recorded" in again.stdout
    assert (workspace / "ledger.md").read_text().count(" base ") == 1


def test_recreated_record_does_not_inherit_old_workspace(project: Path) -> None:
    workspace = _workspace(project)
    _write_proposal_and_plan(workspace)
    _run(project, "push", "r1")
    _run(project, "approve", "r1", "proposal")
    _hiraku(project, "record", "delete", "r1")
    _hiraku(project, "record", "create", "r1", "--type", "task", "--set", "goal=g")

    fresh = _workspace(project)

    assert fresh == workspace
    assert _stage(project) == "define"
    assert list(workspace.parent.glob("r1.stale-*"))


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


def test_finalize_rejects_blank_decision(project: Path) -> None:
    workspace = _workspace(project)
    (workspace / "result.md").write_text("supports\n")
    (workspace / "decision.md").write_text("  \n\n")

    assert _run(project, "finalize", "r1", check=False).returncode == 3


def test_second_writer_is_locked_out(project: Path) -> None:
    workspace = _workspace(project)
    (workspace / ".lock").mkdir()
    (workspace / "summary.md").write_text("## Summary\n\n- Status: proposed\n")

    result = _run(project, "push", "r1", check=False)

    assert result.returncode == 6
    assert "another hiraku-powers operation" in result.stderr
