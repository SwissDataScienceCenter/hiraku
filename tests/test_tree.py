from __future__ import annotations

import pytest

from hiraku_auto.errors import HirakuError
from hiraku_auto.records import load_experiment, save_experiment
from hiraku_auto.scaffold import create_experiment, sync_children
from hiraku_auto.tree import build, render_ascii


def test_tree_is_built_from_parent_links(project):
    forest = build(project)
    assert forest.roots("vo-backend") == ["baseline"]
    assert forest.nodes["baseline"].children == ["candidate"]
    assert forest.nodes["candidate"].parent == "baseline"
    assert forest.depth("candidate") == 1
    assert forest.issues == []


def test_created_child_records_the_inherited_goal_explicitly(project):
    """Records made by the tool are self-describing: the goal is resolved once and written down."""
    create_experiment(project, "grandchild", parent="candidate", expectation="x")
    forest = build(project)
    assert forest.nodes["grandchild"].record.front["goal"] == "vo-backend"
    assert forest.nodes["grandchild"].goal == "vo-backend"
    assert forest.depth("grandchild") == 2


def test_a_hand_written_record_without_a_goal_inherits_it_at_read_time(project):
    """The fallback for records written by hand, so an omitted goal is not an error."""
    create_experiment(project, "grandchild", parent="candidate", expectation="x")
    record = load_experiment(project, "grandchild")
    del record.front["goal"]
    save_experiment(record)
    forest = build(project)
    assert forest.nodes["grandchild"].goal == "vo-backend"
    assert forest.issues == []


def test_reparenting_is_a_one_line_edit(project):
    create_experiment(project, "third", parent="candidate", expectation="x")
    record = load_experiment(project, "third")
    record.front["parent"] = "baseline"
    save_experiment(record)
    forest = build(project)
    assert forest.nodes["baseline"].children == ["candidate", "third"]
    # The directory never moved.
    assert (project.experiments_dir / "third" / "experiment.md").is_file()


def test_dangling_parent_is_reported(project):
    record = load_experiment(project, "candidate")
    record.front["parent"] = "nope"
    save_experiment(record)
    issues = build(project).issues
    assert any("parent 'nope' does not exist" in i for i in issues)


def test_cycle_is_detected_and_does_not_hang(project):
    for node, parent in (("baseline", "candidate"), ("candidate", "baseline")):
        record = load_experiment(project, node)
        record.front["parent"] = parent
        save_experiment(record)
    issues = build(project).issues
    assert sum("cycle" in i for i in issues) == 2


def test_concluded_without_verdict_is_reported(project):
    record = load_experiment(project, "candidate")
    record.front["status"] = "concluded"
    save_experiment(record)
    assert any("concluded without a verdict" in i for i in build(project).issues)


def test_verdict_without_conclusion_is_reported(project):
    record = load_experiment(project, "candidate")
    record.front["verdict"] = "supports"
    save_experiment(record)
    assert any("has a verdict but status is" in i for i in build(project).issues)


def test_missing_expectation_is_reported(project):
    record = load_experiment(project, "candidate")
    record.front["expectation"] = ""
    save_experiment(record)
    assert any("no expectation recorded" in i for i in build(project).issues)


def test_duplicate_ids_are_rejected(project):
    clone = project.experiments_dir / "clone"
    clone.mkdir()
    (clone / "experiment.md").write_text(
        (project.experiments_dir / "candidate" / "experiment.md").read_text(), encoding="utf-8"
    )
    with pytest.raises(HirakuError, match="Two records claim id"):
        build(project)


def test_child_cannot_widen_parent_mode(project):
    with pytest.raises(HirakuError, match="never widen"):
        create_experiment(project, "sneaky", parent="baseline", mode="auto", expectation="x")


def test_sync_writes_children_sections(project):
    sync_children(project)
    body = (project.experiments_dir / "baseline" / "experiment.md").read_text()
    assert "`candidate`" in body.split("## Children")[1]


def test_ascii_render_includes_every_node(project):
    text = render_ascii(build(project))
    assert "baseline" in text and "candidate" in text and "vo-backend" in text
