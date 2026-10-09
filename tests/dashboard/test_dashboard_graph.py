from __future__ import annotations

import pytest

pytest.importorskip("anywidget")

from hiraku.dashboard.data import RecordView
from hiraku.dashboard.graph import RecordGraph, tree_payload
from hiraku.dashboard.tree import compute_layout


def _record(record_id: str, parent_id: str | None = None) -> RecordView:
    return RecordView(
        id=record_id,
        parent_id=parent_id,
        depth=0,
        type=None,
        status=None,
        decision=None,
        commit=None,
        branch=None,
        markdown="",
        fields={},
        sources={},
        metrics=[],
        tracking_backend=None,
    )


def test_tree_payload_includes_visible_nodes_and_expansion() -> None:
    records = [_record("root"), _record("child", parent_id="root")]

    layout = compute_layout(records, selected_id="child", expanded={"root"})
    payload = tree_payload(layout)

    assert [item["id"] for item in payload] == ["root", "child"]
    assert payload[0]["expanded"] is True
    assert payload[1]["selected"] is True
    assert payload[0]["ancestor"] is True
    assert payload[0]["has_children"] is True


def test_record_graph_traits_have_expected_defaults() -> None:
    widget = RecordGraph()

    assert widget.tree == []
    assert widget.selected == ""
    assert widget.expanded == []
    assert widget.query == ""
    assert widget.height == 360


def test_record_graph_esm_is_local_and_interactive() -> None:
    assert "https://" not in RecordGraph._esm
    assert "cdn" not in RecordGraph._esm
    assert "unpkg" not in RecordGraph._esm
    assert "model.set" in RecordGraph._esm
    assert "pointerdown" in RecordGraph._esm
    assert "keydown" in RecordGraph._esm
    assert "hk-tree-list" in RecordGraph._css
