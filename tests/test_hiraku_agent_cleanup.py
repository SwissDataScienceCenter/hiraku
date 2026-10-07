"""Tests for `hiraku-agent delete`: removing a task (or a node) with its subtree."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from hiraku.config import HirakuConfig
from hiraku.record import Record, list_records
from hiraku_agent.cleanup import delete_subtree, plan_delete
from hiraku_agent.cli import main
from hiraku_agent.errors import AgentError
from hiraku_agent.launches import active_launches, mark_running
from hiraku_agent.node import create_node


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


def _tree(config: HirakuConfig) -> Record:
    task = Record.create(
        "matcher", parent_id=None, type="task", config=config, goal="g", question="q"
    )
    create_node("baseline", task=task, kind="baseline", config=config)
    create_node("roma", task=task, parent="baseline", kind="hypothesis", config=config)
    create_node("roma-fix", task=task, parent="roma", kind="diagnostic", config=config)
    Record.create(
        "other", parent_id=None, type="task", config=config, goal="g", question="q"
    )
    return task


def _ids(config: HirakuConfig) -> set[str]:
    return {record.id for record in list_records(config=config)}


def test_deletes_a_whole_task_children_first(config: HirakuConfig) -> None:
    _tree(config)
    config.set_active_record("roma")
    plan = plan_delete("matcher", config=config)
    assert plan.ids.index("roma-fix") < plan.ids.index("roma")
    assert plan.ids.index("roma") < plan.ids.index("baseline")
    assert plan.ids[-1] == "matcher"

    deleted = delete_subtree(plan, config=config)

    assert set(deleted) == {"matcher", "baseline", "roma", "roma-fix"}
    assert _ids(config) == {"other"}
    assert HirakuConfig.discover(config.root).active_record_id is None


def test_deletes_only_a_node_subtree(config: HirakuConfig) -> None:
    _tree(config)
    delete_subtree(plan_delete("roma", config=config), config=config)
    assert _ids(config) == {"matcher", "baseline", "other"}


def test_records_with_runs_need_force(config: HirakuConfig) -> None:
    _tree(config)
    (config.root / "runs.csv").write_text(
        "id,record_id,commit,timestamp,entrypoint,url,metric.recall@10\n"
        "r1,roma,,2026-10-05T10:00:00,train,,0.5\n"
    )
    plan = plan_delete("matcher", config=config)
    assert any("roma has 1 run" in item for item in plan.evidence)
    with pytest.raises(AgentError, match="--force"):
        delete_subtree(plan, config=config)
    assert "roma" in _ids(config)  # nothing deleted on refusal

    delete_subtree(plan, force=True, config=config)
    assert _ids(config) == {"other"}


def test_a_reference_from_outside_blocks_even_with_force(
    config: HirakuConfig,
) -> None:
    _tree(config)
    Record.create(
        "elsewhere",
        parent_id="other",
        config=config,
        related_to=["roma"],
    )
    plan = plan_delete("matcher", config=config)
    assert plan.blockers
    with pytest.raises(AgentError, match="elsewhere"):
        delete_subtree(plan, force=True, config=config)
    assert "matcher" in _ids(config)


def test_a_reference_inside_the_tree_is_deleted_in_the_right_order(
    config: HirakuConfig,
) -> None:
    task = _tree(config)
    create_node(
        "combo", task=task, kind="combination", related_to=["roma"], config=config
    )
    delete_subtree(plan_delete("matcher", config=config), config=config)
    assert _ids(config) == {"other"}


def test_running_markers_are_removed(config: HirakuConfig) -> None:
    _tree(config)
    mark_running("roma", pid=os.getpid(), config=config)
    assert "roma" in active_launches(config)
    delete_subtree(plan_delete("matcher", config=config), config=config)
    assert "roma" not in active_launches(config)


def test_cli_dry_run_lists_and_confirmation_can_abort(
    config: HirakuConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tree(config)
    monkeypatch.chdir(config.root)
    runner = CliRunner()

    dry = runner.invoke(main, ["delete", "matcher", "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "would delete 4 record(s)" in dry.output
    assert len(_ids(config)) == 5

    aborted = runner.invoke(main, ["delete", "matcher"], input="n\n")
    assert aborted.exit_code != 0
    assert len(_ids(config)) == 5

    done = runner.invoke(main, ["delete", "matcher", "--yes"])
    assert done.exit_code == 0, done.output
    assert "deleted 4 record(s)" in done.output
    assert _ids(config) == {"other"}


def test_unknown_id_is_a_clear_error(config: HirakuConfig) -> None:
    with pytest.raises(AgentError, match="No record"):
        plan_delete("nope", config=config)
