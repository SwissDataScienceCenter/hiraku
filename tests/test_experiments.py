from __future__ import annotations

import pytest

from hiraku.errors import HirakuError


def test_siblings_share_goal_baseline_and_auto_checkout(project):
    _, app = project
    baseline = app.goals.get("demo").baseline_commit
    first = app.experiments.create("demo/first", "first attempt")
    assert app.git.current_branch() == first.branch
    assert app.git.head_commit() == baseline
    second = app.experiments.create("demo/second", "second attempt")
    assert app.git.current_branch() == second.branch
    assert second.parent_commit == baseline


def test_child_starts_from_parent_frozen_commit(project):
    root, app = project
    parent = app.experiments.create("demo/parent", "parent")
    (root / "change.txt").write_text("parent", encoding="utf-8")
    app.git._run("add", "change.txt")
    app.git._run("commit", "-m", "parent implementation")
    frozen = app.git.head_commit()
    app.experiments.freeze(parent, frozen)
    child = app.experiments.create("demo/child", "child", parent="demo/parent")
    assert child.parent_commit == frozen
    assert app.git.head_commit() == frozen


def test_child_requires_evaluated_parent(project):
    _, app = project
    app.experiments.create("demo/parent", "parent")
    with pytest.raises(HirakuError, match="frozen evaluated commit"):
        app.experiments.create("demo/child", "child", parent="demo/parent")


def test_baseline_uniqueness_rolls_back_git(project):
    _, app = project
    first = app.experiments.create("demo/reference", "reference", kind="baseline")
    original = first.branch
    with pytest.raises(HirakuError, match="already has a baseline"):
        app.experiments.create("demo/reference-2", "other", kind="baseline")
    assert app.git.current_branch() == original
    assert not app.git.branch_exists("hiraku/demo/reference-2")


def test_freeze_rejects_later_commit(project):
    root, app = project
    experiment = app.experiments.create("demo/frozen", "frozen")
    first = app.git.head_commit()
    app.experiments.freeze(experiment, first)
    (root / "later.txt").write_text("later", encoding="utf-8")
    app.git._run("add", "later.txt")
    app.git._run("commit", "-m", "later")
    with pytest.raises(HirakuError, match="create a child or sibling"):
        app.experiments.freeze(app.experiments.get("demo/frozen"), app.git.head_commit())


def test_lineage_tree(project):
    _, app = project
    parent = app.experiments.create("demo/parent", "parent")
    app.experiments.freeze(parent, app.git.head_commit())
    app.experiments.create("demo/child", "child", parent="demo/parent")
    tree = app.lineage.forest("demo")
    assert tree[0]["id"] == "parent"
    assert tree[0]["children"][0]["id"] == "child"


def test_lineage_cycle_is_detected(project):
    _, app = project
    first = app.experiments.create("demo/first", "first")
    app.experiments.freeze(first, app.git.head_commit())
    app.experiments.create("demo/second", "second", parent="demo/first")
    with app.db.transaction() as connection:
        connection.execute(
            "UPDATE experiments SET parent_id='second' WHERE goal_id='demo' AND id='first'"
        )
    with pytest.raises(HirakuError, match="cycle"):
        app.lineage.forest("demo")
