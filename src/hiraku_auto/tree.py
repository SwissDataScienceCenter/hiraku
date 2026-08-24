"""Building the experiment tree from declared `parent` links, and checking it.

The tree is always computed, never stored: read every record, group by goal, link by parent. That
is what makes reparenting a one-line edit instead of a directory move.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .config import Project
from .records import (
    KINDS,
    ORIGINS,
    STATUSES,
    VERDICTS,
    Record,
    load_experiments,
    load_goals,
    load_proposals,
)
from .runs import RunTable, read_runs


@dataclass
class Node:
    id: str
    record: Record
    goal: str | None = None
    parent: str | None = None
    parents: list[str] = field(default_factory=list)
    children: list[str] = field(default_factory=list)
    runs: list[dict[str, str]] = field(default_factory=list)

    @property
    def front(self) -> dict[str, Any]:
        return self.record.front

    @property
    def status(self) -> str:
        return str(self.front.get("status") or "")

    @property
    def verdict(self) -> str:
        return str(self.front.get("verdict") or "")

    @property
    def kind(self) -> str:
        return str(self.front.get("kind") or "")


@dataclass
class Forest:
    project: Project
    nodes: dict[str, Node]
    goals: dict[str, Record]
    proposals: dict[str, Record]
    table: RunTable
    issues: list[str] = field(default_factory=list)

    def roots(self, goal_id: str) -> list[str]:
        return sorted(
            node_id
            for node_id, node in self.nodes.items()
            if node.goal == goal_id
            and not (node.parent in self.nodes or any(p in self.nodes for p in node.parents))
        )

    def subtree(self, node_id: str) -> list[str]:
        """Node IDs in the subtree rooted at node_id, including itself, depth-first."""
        out: list[str] = []
        seen: set[str] = set()

        def walk(current: str) -> None:
            if current in seen or current not in self.nodes:
                return
            seen.add(current)
            out.append(current)
            for child in self.nodes[current].children:
                walk(child)

        walk(node_id)
        return out

    def depth(self, node_id: str) -> int:
        depth = 0
        seen: set[str] = set()
        cursor = self.nodes[node_id].parent if node_id in self.nodes else None
        while cursor and cursor in self.nodes and cursor not in seen:
            seen.add(cursor)
            depth += 1
            cursor = self.nodes[cursor].parent
        return depth

    def goal_nodes(self, goal_id: str) -> list[Node]:
        return [n for n in self.nodes.values() if n.goal == goal_id]

    def unattached(self) -> list[str]:
        placed = {nid for gid in self.goals for nid in self.subtree_all(gid)}
        return sorted(set(self.nodes) - placed)

    def subtree_all(self, goal_id: str) -> list[str]:
        out: list[str] = []
        for root in self.roots(goal_id):
            out.extend(self.subtree(root))
        return out


def build(project: Project) -> Forest:
    records = load_experiments(project)
    goals = load_goals(project)
    proposals = load_proposals(project)
    table = read_runs(project.table_path)

    nodes: dict[str, Node] = {}
    for node_id, record in records.items():
        front = record.front
        parents = front.get("parents")
        nodes[node_id] = Node(
            id=node_id,
            record=record,
            goal=str(front["goal"]) if front.get("goal") else None,
            parent=str(front["parent"]) if front.get("parent") else None,
            parents=[str(p) for p in parents] if isinstance(parents, list) else [],
        )

    # Inherit goal along the parent chain.
    for node in nodes.values():
        if node.goal:
            continue
        seen: set[str] = set()
        cursor = node.parent
        while cursor and cursor in nodes and cursor not in seen:
            seen.add(cursor)
            inherited = nodes[cursor].front.get("goal")
            if inherited:
                node.goal = str(inherited)
                break
            cursor = nodes[cursor].parent

    for node in nodes.values():
        for parent_id in ([node.parent] if node.parent else []) + node.parents:
            if parent_id in nodes:
                nodes[parent_id].children.append(node.id)
    for node in nodes.values():
        node.children.sort()
        node.runs = [r for r in table.rows if r.get("experiment_id") == node.id]

    forest = Forest(
        project=project, nodes=nodes, goals=goals, proposals=proposals, table=table
    )
    forest.issues = check(forest)
    return forest


def check(forest: Forest) -> list[str]:
    """Every consistency problem the records can express. Returns human-readable strings."""
    issues: list[str] = []
    nodes, goals = forest.nodes, forest.goals

    for node in nodes.values():
        front = node.front
        nid = node.id

        if node.record.path.parent.name != nid:
            issues.append(
                f"{nid}: record id disagrees with directory '{node.record.path.parent.name}'"
            )
        if not node.goal:
            issues.append(f"{nid}: no goal, and none inherited from a parent")
        elif node.goal not in goals:
            issues.append(f"{nid}: goal '{node.goal}' does not exist")

        for parent_id in ([node.parent] if node.parent else []) + node.parents:
            if parent_id not in nodes:
                issues.append(f"{nid}: parent '{parent_id}' does not exist")
                continue
            parent_goal = nodes[parent_id].goal
            if parent_goal and node.goal and parent_goal != node.goal:
                issues.append(
                    f"{nid}: goal '{node.goal}' disagrees with "
                    f"parent '{parent_id}' ('{parent_goal}')"
                )

        status, verdict, kind = node.status, node.verdict, node.kind
        origin = str(front.get("origin") or "")
        mode = str(front.get("mode") or "")
        if status and status not in STATUSES:
            issues.append(f"{nid}: unknown status '{status}'")
        if kind and kind not in KINDS:
            issues.append(f"{nid}: unknown kind '{kind}'")
        if verdict and verdict not in VERDICTS:
            issues.append(f"{nid}: unknown verdict '{verdict}'")
        if origin and origin not in ORIGINS:
            issues.append(f"{nid}: unknown origin '{origin}'")
        if status == "concluded" and not verdict:
            issues.append(f"{nid}: concluded without a verdict")
        if verdict and status != "concluded":
            issues.append(f"{nid}: has a verdict but status is '{status}'")
        if not front.get("expectation"):
            issues.append(f"{nid}: no expectation recorded")

        # A child may narrow its parent's mode, never widen it.
        if node.parent in nodes and mode == "auto":
            parent_mode = str(nodes[node.parent].front.get("mode") or "")
            if parent_mode == "guided":
                issues.append(
                    f"{nid}: mode 'auto' widens its parent '{node.parent}' which is 'guided'"
                )

    for node_id in nodes:
        seen: set[str] = set()
        cursor = nodes[node_id].parent
        while cursor and cursor in nodes:
            if cursor == node_id or cursor in seen:
                issues.append(f"{node_id}: parent chain contains a cycle")
                break
            seen.add(cursor)
            cursor = nodes[cursor].parent

    known = set(nodes)
    for row in forest.table.rows:
        experiment_id = row.get("experiment_id", "")
        if experiment_id and experiment_id not in known:
            issues.append(f"run {row.get('run_id', '?')}: unknown experiment '{experiment_id}'")

    return sorted(set(issues))


def render_ascii(forest: Forest, goal_id: str | None = None) -> str:
    """The tree as indented text, for the terminal."""
    lines: list[str] = []
    goal_ids: Iterable[str] = [goal_id] if goal_id else forest.goals.keys()

    for gid in goal_ids:
        goal = forest.goals.get(gid)
        if goal is None:
            continue
        front = goal.front
        arrow = "v" if front.get("direction") == "minimize" else "^"
        target = front.get("target")
        head = f"{gid}   [{front.get('primary_metric', '?')} {arrow}"
        head += f", target {target}]" if target is not None else "]"
        lines.append(head)

        def walk(node_id: str, prefix: str, last: bool) -> None:
            node = forest.nodes[node_id]
            connector = "`-- " if last else "|-- "
            bits = [b for b in (node.kind, node.status, node.verdict) if b]
            origin = str(node.front.get("origin") or "")
            if origin.startswith("agent"):
                bits.append(origin)
            if node.runs:
                bits.append(f"{len(node.runs)} run{'s' if len(node.runs) != 1 else ''}")
            lines.append(f"{prefix}{connector}{node_id}  ({', '.join(bits)})" if bits
                         else f"{prefix}{connector}{node_id}")
            children = node.children
            for index, child in enumerate(children):
                walk(child, prefix + ("    " if last else "|   "), index == len(children) - 1)

        roots = forest.roots(gid)
        for index, root in enumerate(roots):
            walk(root, "", index == len(roots) - 1)
        if not roots:
            lines.append("  (no experiments)")
        lines.append("")

    orphans = forest.unattached()
    if orphans and goal_id is None:
        lines.append("unattached (no reachable goal):")
        lines.extend(f"  {o}" for o in orphans)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def to_dict(forest: Forest) -> dict[str, Any]:
    """JSON-serialisable snapshot. This is what the agent is shown and what the viewer consumes."""
    return {
        "root": str(forest.project.root),
        "goals": {
            gid: {
                "id": gid,
                "front": record.front,
                "sections": record.sections,
                "roots": forest.roots(gid),
            }
            for gid, record in forest.goals.items()
        },
        "experiments": {
            nid: {
                "id": nid,
                "front": node.front,
                "goal": node.goal,
                "parent": node.parent,
                "parents": node.parents,
                "children": node.children,
                "depth": forest.depth(nid),
                "sections": node.record.sections,
                "runs": node.runs,
                "path": forest.project.rel(node.record.path),
            }
            for nid, node in forest.nodes.items()
        },
        "proposals": {
            pid: {"id": pid, "front": record.front, "sections": record.sections}
            for pid, record in forest.proposals.items()
        },
        "runs": forest.table.rows,
        "issues": forest.issues,
    }
