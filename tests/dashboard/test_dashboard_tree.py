from __future__ import annotations

from hiraku.dashboard.data import RecordView
from hiraku.dashboard.tree import (
    build_tree,
    compute_layout,
    reconcile_navigation,
    reveal_search,
)


def _record(
    record_id: str,
    parent_id: str | None = None,
    markdown: str = "",
) -> RecordView:
    return RecordView(
        id=record_id,
        parent_id=parent_id,
        depth=0,
        type=None,
        status=None,
        decision=None,
        commit=None,
        branch=None,
        markdown=markdown,
        fields={},
        sources={},
        metrics=[],
        tracking_backend=None,
    )


def test_empty_forest() -> None:
    layout = build_tree([])

    assert layout.nodes == {}
    assert layout.roots == ()
    assert layout.visible == ()
    assert layout.disconnected == ()
    assert layout.edges == ()
    assert compute_layout([]).visible == ()
    assert reconcile_navigation([], selected_id="x", expanded={"x"}).selected_id is None


def test_multiple_roots_and_sorted_siblings() -> None:
    records = [
        _record("b"),
        _record("a"),
        _record("b-2", parent_id="b"),
        _record("b-1", parent_id="b"),
        _record("a-1", parent_id="a"),
    ]
    layout = build_tree(records)

    assert layout.roots == ("a", "b")
    assert layout.edges == (("a", "a-1"), ("b", "b-1"), ("b", "b-2"))
    assert layout.visible == ("a", "b")

    expanded = compute_layout(records, expanded={"b"})
    assert expanded.visible == ("a", "b", "b-1", "b-2")
    y_positions = [expanded.nodes[record_id].y for record_id in expanded.visible]
    assert y_positions == [0, 1, 2, 3]


def test_collapsed_branches_hide_their_descendants() -> None:
    records = [
        _record("root"),
        _record("child", parent_id="root"),
        _record("grandchild", parent_id="child"),
        _record("sibling", parent_id="root"),
    ]

    assert compute_layout(records).visible == ("root",)
    assert compute_layout(records, expanded={"root"}).visible == (
        "root",
        "child",
        "sibling",
    )
    assert compute_layout(records, expanded={"root", "child"}).visible == (
        "root",
        "child",
        "grandchild",
        "sibling",
    )


def test_search_reveals_collapsed_matches_without_expanding_them() -> None:
    records = [
        _record("root"),
        _record("child", parent_id="root"),
        _record("leaf", parent_id="child", markdown="the target description"),
    ]

    revealed = reveal_search(records, "target")

    assert revealed == {"root", "child", "leaf"}
    layout = compute_layout(records, search="target")
    assert layout.visible == ("root", "child", "leaf")
    assert layout.search_matches == ("leaf",)
    assert layout.nodes["leaf"].search_match is True


def test_removed_selection_and_expansion_are_reported_explicitly() -> None:
    records = [_record("a"), _record("b")]

    state = reconcile_navigation(
        records,
        selected_id="gone",
        expanded={"a", "gone", "also-gone"},
    )

    assert state.selected_id is None
    assert state.removed_selection is True
    assert state.expanded == frozenset({"a"})
    assert state.removed_expanded == ("also-gone", "gone")


def test_existing_selection_and_ancestry_are_preserved() -> None:
    records = [_record("root"), _record("child", parent_id="root")]
    layout = compute_layout(records, selected_id="child", expanded={"root"})

    assert layout.visible == ("root", "child")
    assert layout.nodes["child"].selected is True
    assert layout.nodes["root"].ancestor_of_selection is True
    assert layout.nodes["root"].ancestors == ()
    assert layout.nodes["child"].ancestors == ("root",)
    assert layout.nodes["root"].x == 0
    assert layout.nodes["child"].x == 1


def test_deep_chain_uses_iterative_traversal() -> None:
    records = [
        _record(f"r{i}", parent_id=(f"r{i - 1}" if i else None)) for i in range(1000)
    ]

    layout = compute_layout(records, expanded={f"r{i}" for i in range(999)})

    assert len(layout.visible) == 1000
    assert layout.visible[0] == "r0"
    assert layout.visible[-1] == "r999"
    assert layout.nodes["r999"].depth == 999
    assert layout.nodes["r999"].x == 999


def test_missing_parent_is_disconnected_and_reachable() -> None:
    records = [
        _record("root"),
        _record("orphan", parent_id="ghost"),
        _record("child", parent_id="orphan"),
    ]

    layout = build_tree(records)

    assert layout.roots == ("root",)
    assert layout.disconnected == ("child", "orphan")
    assert "orphan" in layout.visible
    assert "child" in layout.visible
    assert layout.nodes["orphan"].disconnected is True
    assert layout.nodes["child"].disconnected is True


def test_parent_cycle_is_disconnected_not_invented() -> None:
    records = [
        _record("a", parent_id="b"),
        _record("b", parent_id="a"),
        _record("c", parent_id="a"),
    ]

    layout = build_tree(records)

    assert layout.roots == ()
    assert layout.disconnected == ("a", "b", "c")
    assert layout.edges == ()
    assert layout.visible == ("a", "b", "c")


def test_disconnected_group_has_separate_coordinates() -> None:
    records = [_record("root"), _record("orphan", parent_id="ghost")]

    layout = compute_layout(records)

    assert layout.visible == ("root", "orphan")
    assert layout.nodes["root"].x == 0
    assert layout.nodes["orphan"].x == 0
    assert layout.nodes["root"].y == 0
    assert layout.nodes["orphan"].y == 1
