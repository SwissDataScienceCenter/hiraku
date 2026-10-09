"""Pure record hierarchy projection for the dashboard tree graph.

These helpers turn a flat sequence of :class:`~hiraku.dashboard.data.RecordView`
objects into an adjacency structure, deterministic visible layout, and
navigation-state reconciliation. They never touch the filesystem and never
query a tracking backend.

Contracts exported for GUI-04
-----------------------------

``build_tree(records) -> TreeLayout``
    Builds adjacency, roots, ancestry, and disconnected groups. Disconnected
    records (missing parent or cycle) are kept reachable as a separate,
    clearly marked group and are never given invented parentage.

``compute_layout(records, *, selected_id, expanded, search) -> TreeLayout``
    Returns a TreeLayout whose ``visible`` order and node coordinates include
    expansion and search reveal. Siblings are sorted by ID and traversed
    depth-first, so coordinates are stable across equivalent inputs.

``reconcile_navigation(records, *, selected_id, expanded) -> NavigationState``
    Drops selections/expansions whose IDs no longer exist and reports them.

``reveal_search(records, search) -> set[str]``
    Returns IDs matched by a case-insensitive search over record IDs and
    markdown descriptions plus all of their valid ancestors.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass

from .data import RecordView


@dataclass(frozen=True)
class TreeNode:
    """One record projected into tree coordinates."""

    id: str
    parent_id: str | None
    depth: int
    x: int
    y: int
    selected: bool
    ancestor_of_selection: bool
    disconnected: bool
    expanded: bool
    children: tuple[str, ...]
    ancestors: tuple[str, ...]
    search_match: bool


@dataclass(frozen=True)
class TreeLayout:
    """Adjacency, visibility, and coordinates for the record graph."""

    nodes: Mapping[str, TreeNode]
    roots: tuple[str, ...]
    visible: tuple[str, ...]
    disconnected: tuple[str, ...]
    edges: tuple[tuple[str, str], ...]
    search_matches: tuple[str, ...]


@dataclass(frozen=True)
class NavigationState:
    """Reconciled selection and expansion for one refresh."""

    selected_id: str | None
    expanded: frozenset[str]
    removed_selection: bool
    removed_expanded: tuple[str, ...]


def _normalized_records(
    records: Iterable[RecordView],
) -> dict[str, RecordView]:
    """Index records by ID, keeping the first occurrence deterministically."""
    index: dict[str, RecordView] = {}
    for record in sorted(records, key=lambda item: item.id):
        index.setdefault(record.id, record)
    return index


def _parent_map(
    index: Mapping[str, RecordView],
) -> dict[str, str | None]:
    """Map each record to its existing parent, with ``None`` for roots."""
    ids = set(index)
    result: dict[str, str | None] = {}
    for record_id, record in index.items():
        parent_id = record.parent_id
        result[record_id] = parent_id if parent_id in ids else None
    return result


def _classify(
    index: Mapping[str, RecordView], parent: Mapping[str, str | None]
) -> dict[str, bool]:
    """Classify each record as connected (``True``) or disconnected."""
    status: dict[str, bool] = {}
    for start in index:
        if start in status:
            continue
        path: list[str] = []
        path_set: set[str] = set()
        current: str | None = start
        result: bool | None = None
        while current is not None:
            if current in status:
                result = status[current]
                break
            if current in path_set:
                result = False
                break
            path.append(current)
            path_set.add(current)
            record = index[current]
            if record.parent_id is None:
                result = True
                break
            parent_id = parent[current]
            if parent_id is None:
                # The parent is missing, not a root boundary.
                result = False
                break
            current = parent_id
        for record_id in reversed(path):
            status[record_id] = bool(result)
    return status


def _children(
    index: Mapping[str, RecordView],
    parent: Mapping[str, str | None],
    connected: Mapping[str, bool],
) -> dict[str, list[str]]:
    children: dict[str, list[str]] = {record_id: [] for record_id in index}
    for record_id, parent_id in parent.items():
        if parent_id is not None and connected[record_id] and connected[parent_id]:
            children[parent_id].append(record_id)
    for values in children.values():
        values.sort()
    return children


def _ancestors(
    parent: Mapping[str, str | None],
    connected: Mapping[str, bool],
    selected_id: str | None,
) -> dict[str, tuple[str, ...]]:
    """Return valid ancestor chains for each record, furthest root last."""
    ancestors: dict[str, tuple[str, ...]] = {}
    if selected_id is None or not connected.get(selected_id, False):
        for record_id in parent:
            ancestors[record_id] = ()
        return ancestors
    for record_id in parent:
        chain: list[str] = []
        current = parent[record_id]
        seen: set[str] = set()
        while current is not None and current not in seen:
            chain.append(current)
            seen.add(current)
            current = parent[current]
        ancestors[record_id] = tuple(chain)
    return ancestors


def build_tree(records: Iterable[RecordView]) -> TreeLayout:
    """Build adjacency and connected/disconnected groups.

    Records with a missing parent or a parent cycle are placed in the
    ``disconnected`` group. Their parent links are not rendered as edges, so
    the graph never invents valid parentage for broken hierarchies.
    """
    index = _normalized_records(records)
    parent = _parent_map(index)
    connected = _classify(index, parent)
    children = _children(index, parent, connected)
    roots = tuple(
        record_id
        for record_id in sorted(index)
        if connected[record_id] and parent[record_id] is None
    )
    disconnected = tuple(
        record_id for record_id in sorted(index) if not connected[record_id]
    )
    edges = tuple(
        (record_id, child_id)
        for record_id in sorted(children)
        for child_id in children[record_id]
    )
    nodes: dict[str, TreeNode] = {}
    for record_id in index:
        nodes[record_id] = TreeNode(
            id=record_id,
            parent_id=index[record_id].parent_id,
            depth=0,
            x=0,
            y=0,
            selected=False,
            ancestor_of_selection=False,
            disconnected=not connected[record_id],
            expanded=False,
            children=tuple(children[record_id]),
            ancestors=(),
            search_match=False,
        )
    return TreeLayout(
        nodes=nodes,
        roots=roots,
        visible=(*roots, *disconnected),
        disconnected=disconnected,
        edges=edges,
        search_matches=(),
    )


def reveal_search(records: Iterable[RecordView], search: str) -> set[str]:
    """Return search matches plus their valid ancestors."""
    index = _normalized_records(records)
    parent = _parent_map(index)
    connected = _classify(index, parent)
    query = search.strip().casefold()
    matches: list[str] = []
    for record_id in sorted(index):
        record = index[record_id]
        if query and (
            query in record.id.casefold() or query in record.markdown.casefold()
        ):
            matches.append(record_id)
    revealed: set[str] = set()
    for record_id in matches:
        if not connected.get(record_id, False):
            revealed.add(record_id)
            continue
        current: str | None = record_id
        seen: set[str] = set()
        while current is not None and current not in seen:
            revealed.add(current)
            seen.add(current)
            current = parent[current]
    return revealed


def compute_layout(
    records: Iterable[RecordView],
    *,
    selected_id: str | None = None,
    expanded: Iterable[str] = (),
    search: str = "",
) -> TreeLayout:
    """Compute a deterministic visible layout.

    Roots and disconnected groups are always visible. Children are visible
    when their parent is expanded; search matches reveal their ancestry even
    when collapsed. Siblings are sorted by ID and laid out depth-first.
    """
    base = build_tree(records)
    parent = _parent_map(_normalized_records(records))
    connected = {
        record_id: not node.disconnected for record_id, node in base.nodes.items()
    }
    children = {
        record_id: list(node.children) for record_id, node in base.nodes.items()
    }
    ancestors = _ancestors(parent, connected, selected_id)
    selected_ancestors = set(ancestors.get(selected_id, ())) if selected_id else set()
    effective_expanded = {
        record_id for record_id in expanded if record_id in base.nodes
    }
    effective_expanded.update(selected_ancestors)
    effective_expanded.update(reveal_search(records, search))
    query = search.strip().casefold()
    index = _normalized_records(records)
    search_matches = tuple(
        record_id
        for record_id in sorted(index)
        if query
        and (
            query in index[record_id].id.casefold()
            or query in index[record_id].markdown.casefold()
        )
    )

    visible: list[str] = []
    for root in base.roots:
        visible.append(root)
        if root in effective_expanded:
            _append_visible_children(root, children, effective_expanded, visible)

    # Disconnected records stay reachable in a clearly marked separate group.
    for record_id in base.disconnected:
        visible.append(record_id)

    nodes: dict[str, TreeNode] = {}
    for position, record_id in enumerate(visible):
        node = base.nodes[record_id]
        depth = _node_depth(record_id, parent, connected)
        nodes[record_id] = TreeNode(
            id=node.id,
            parent_id=node.parent_id,
            depth=depth,
            x=0 if node.disconnected else depth,
            y=position,
            selected=node.id == selected_id,
            ancestor_of_selection=node.id in selected_ancestors,
            disconnected=node.disconnected,
            expanded=bool(node.children and record_id in effective_expanded),
            children=node.children,
            ancestors=ancestors[node.id],
            search_match=node.id in search_matches,
        )
    return TreeLayout(
        nodes=nodes,
        roots=base.roots,
        visible=tuple(visible),
        disconnected=base.disconnected,
        edges=base.edges,
        search_matches=search_matches,
    )


def _append_visible_children(
    root_id: str,
    children: Mapping[str, Sequence[str]],
    expanded: set[str],
    visible: list[str],
) -> None:
    """Append expanded descendants depth-first using an explicit stack."""
    stack: list[Iterator[str]] = [iter(children.get(root_id, ()))]
    while stack:
        try:
            child_id = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        visible.append(child_id)
        if child_id in expanded:
            stack.append(iter(children.get(child_id, ())))


def _node_depth(
    record_id: str,
    parent: Mapping[str, str | None],
    connected: Mapping[str, bool],
) -> int:
    if not connected.get(record_id, False):
        return 0
    depth = 0
    current = parent[record_id]
    seen: set[str] = set()
    while current is not None and current not in seen:
        depth += 1
        seen.add(current)
        current = parent[current]
    return depth


def reconcile_navigation(
    records: Iterable[RecordView],
    *,
    selected_id: str | None = None,
    expanded: Iterable[str] = (),
) -> NavigationState:
    """Reconcile selection and expansion after a record refresh.

    Missing IDs are dropped explicitly instead of being silently replaced with
    an unrelated record. Callers can use ``removed_selection`` to show a
    message and ``removed_expanded`` to prune stale UI state.
    """
    ids = set(_normalized_records(records))
    removed_selection = selected_id is not None and selected_id not in ids
    selected = None if removed_selection else selected_id
    expanded_set = set(expanded)
    removed_expanded = tuple(
        sorted(record_id for record_id in expanded_set if record_id not in ids)
    )
    return NavigationState(
        selected_id=selected,
        expanded=frozenset(expanded_set - set(removed_expanded)),
        removed_selection=removed_selection,
        removed_expanded=removed_expanded,
    )
