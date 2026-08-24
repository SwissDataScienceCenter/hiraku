from __future__ import annotations

import pytest

from hiraku_auto import gitops
from hiraku_auto import promote as promote_mod
from hiraku_auto.errors import HirakuError
from hiraku_auto.launcher import launch, resolve_entrypoint
from hiraku_auto.runs import append_run, rank, read_runs


def _launch(project, **kwargs):
    return launch(project, "candidate", "train", **kwargs)


def test_worktree_launch_is_marked_non_reproducible(entrypoint):
    result = _launch(entrypoint, seed=1)
    assert result.exit_code == 0
    assert result.row["run_kind"] == "worktree"
    assert result.row["reproducible"] == "false"
    assert result.row["ate_rmse_m"] == 2.001


def test_commit_launch_is_reproducible_and_leaves_no_worktree(entrypoint):
    before = len(gitops.worktree_list(entrypoint.root))
    result = _launch(entrypoint, commit="HEAD", seed=2)
    assert result.row["reproducible"] == "true"
    assert result.row["commit"] == gitops.head_commit(entrypoint.root)
    assert len(gitops.worktree_list(entrypoint.root)) == before


def test_provenance_columns_are_recorded(entrypoint):
    result = _launch(entrypoint, commit="HEAD", seed=1)
    for column in ("run_id", "launched_at", "entrypoint", "python_version", "platform"):
        assert result.row[column], f"{column} is empty"


def test_metrics_come_from_the_metrics_file(entrypoint):
    result = _launch(entrypoint, commit="HEAD", seed=7)
    assert result.row["ate_rmse_m"] == 2.007
    assert result.row["completion_rate"] == 0.9


def test_unknown_entrypoint_lists_the_available_ones(entrypoint):
    with pytest.raises(HirakuError, match="Available: train"):
        launch(entrypoint, "candidate", "nope")


def test_entrypoint_cannot_escape_the_project():
    with pytest.raises(HirakuError, match="cwd outside"):
        resolve_entrypoint({"x": {"command": ["true"], "cwd": "../.."}}, "x")


def test_entrypoint_cannot_set_reserved_variables():
    with pytest.raises(HirakuError, match="reserved variable"):
        resolve_entrypoint({"x": {"command": ["true"], "env": {"HIRAKU_RUN_ID": "1"}}}, "x")


def test_table_widens_without_losing_existing_rows(bare_project):
    path = bare_project.table_path
    append_run(path, {"experiment_id": "a", "commit": "x", "one": 1})
    append_run(path, {"experiment_id": "b", "commit": "y", "two": 2})
    table = read_runs(path)
    assert [r["experiment_id"] for r in table.rows] == ["a", "b"]
    assert table.rows[0]["one"] == "1" and table.rows[0]["two"] == ""
    assert table.rows[1]["two"] == "2"


def test_rank_puts_non_numeric_last():
    rows = [{"m": "2"}, {"m": ""}, {"m": "1"}, {"m": "nan"}]
    ranked = rank(rows, "m", descending=False)
    assert [r.get("_rank") for r in ranked] == [1, 2, None, None]
    assert ranked[0]["m"] == "1"


# --------------------------------------------------------------------------- the gate


def _two_seeds(project):
    a = _launch(project, commit="HEAD", seed=1)
    b = _launch(project, commit="HEAD", seed=2)
    return a, b


def test_working_tree_run_is_never_promotable(entrypoint):
    result = _launch(entrypoint, seed=1)
    gate = promote_mod.evaluate(entrypoint, "candidate", result.run_id, signed_off_by="Q")
    failed = {c.name for c in gate.failures()}
    assert "reproducible" in failed
    assert not gate.passed


def test_gate_passes_with_reproduction_and_signoff(entrypoint):
    _set_tolerance(entrypoint, 0.05)
    good, _ = _two_seeds(entrypoint)
    gate = promote_mod.evaluate(entrypoint, "candidate", good.run_id, signed_off_by="Q")
    assert gate.passed, {c.name: c.detail for c in gate.failures()}
    assert gate.reproduction is not None


def test_gate_fails_without_signoff_in_guided_mode(entrypoint):
    _set_tolerance(entrypoint, 0.05)
    good, _ = _two_seeds(entrypoint)
    gate = promote_mod.evaluate(entrypoint, "candidate", good.run_id)
    assert "signed_off" in {c.name for c in gate.failures()}


def test_gate_detects_a_changed_evaluation_protocol(entrypoint):
    _set_tolerance(entrypoint, 0.05)
    good, _ = _two_seeds(entrypoint)
    goal = entrypoint.goals_dir / "vo-backend.md"
    goal.write_text(goal.read_text() + "\n- a new split rule\n", encoding="utf-8")
    gate = promote_mod.evaluate(entrypoint, "candidate", good.run_id, signed_off_by="Q")
    assert "protocol" in {c.name for c in gate.failures()}


def test_promote_appends_to_the_leaderboard(entrypoint):
    _set_tolerance(entrypoint, 0.05)
    good, _ = _two_seeds(entrypoint)
    result = promote_mod.promote(entrypoint, "candidate", good.run_id, signed_off_by="Q")
    assert result.passed
    board = entrypoint.leaderboard.read_text(encoding="utf-8")
    assert good.run_id in board and "candidate" in board


def test_failing_gate_writes_nothing(entrypoint):
    result = _launch(entrypoint, seed=1)
    before = entrypoint.leaderboard.read_text(encoding="utf-8")
    promote_mod.promote(entrypoint, "candidate", result.run_id)
    assert entrypoint.leaderboard.read_text(encoding="utf-8") == before


def _set_tolerance(project, value: float) -> None:
    path = project.config_md
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "agreement_tolerance: 0.01", f"agreement_tolerance: {value}"
        ),
        encoding="utf-8",
    )
    gitops.run_git(project.root, "add", "-A")
    gitops.run_git(project.root, "commit", "-qm", "tolerance")
