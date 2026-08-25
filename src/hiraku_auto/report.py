"""The goal report: what was asked, what is known, what was ruled out, what is still open.

Deterministic. Every sentence of judgement in the output is **quoted from a record** — the
Findings and Conclusions an agent or a researcher wrote, with the run IDs they cite. This module
assembles and orders them; it never writes prose of its own about results, and never calls a model.

That is what makes the report safe to regenerate and diff: if it changes, a record changed.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from .config import Project
from .records import read_record
from .runs import best, rank
from .tree import Forest, build, render_ascii


def _excerpt(text: str, limit: int = 1200) -> str:
    text = (text or "").strip()
    if not text:
        return "_Not recorded._"
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit("\n\n", 1)[0]
    return cut.rstrip() + "\n\n_(truncated — full text in the record)_"


def _goal_meta(front: dict[str, Any]) -> str:
    metric = front.get("primary_metric") or "?"
    direction = front.get("direction") or "minimize"
    arrow = "lower is better" if direction == "minimize" else "higher is better"
    bits = [f"`{metric}` ({arrow})"]
    if front.get("target") is not None:
        bits.append(f"target **{front['target']}**")
    if front.get("gate_metric"):
        bits.append(f"gate metric `{front['gate_metric']}` must not degrade")
    bits.append(f"status *{front.get('status', 'active')}*")
    return " · ".join(bits)


def _node_line(forest: Forest, node_id: str) -> str:
    node = forest.nodes[node_id]
    bits = [b for b in (node.kind, node.status, node.verdict) if b]
    origin = str(node.front.get("origin") or "")
    if origin.startswith("agent"):
        bits.append(origin)
    return f"`{node_id}` — {', '.join(bits)}" if bits else f"`{node_id}`"


def render(project: Project, goal_id: str, forest: Forest | None = None) -> str:
    forest = forest or build(project)
    if goal_id not in forest.goals:
        from .errors import HirakuError

        raise HirakuError(f"Goal '{goal_id}' does not exist.")

    goal = forest.goals[goal_id]
    front = goal.front
    metric = str(front.get("primary_metric") or "")
    direction = str(front.get("direction") or "minimize")
    node_ids = forest.subtree_all(goal_id)
    nodes = [forest.nodes[n] for n in node_ids]
    runs = [r for n in nodes for r in n.runs]

    project_name = project.root.name
    if project.config_md.is_file():
        project_name = read_record(project.config_md).front.get("project") or project_name

    out: list[str] = []
    add = out.append

    add(f"# {goal_id} — report\n")
    add(
        f"{project_name} · generated {_dt.date.today().isoformat()} from the records in "
        f"`{project.rel(project.research_dir)}` and `{project.rel(project.experiments_dir)}`.\n"
    )
    add(
        "Assembled from the records, not written by a model: every judgement below is quoted from "
        "an experiment's Findings or Conclusions. Regenerate it any time; if it changes, a record "
        "changed.\n"
    )

    # ------------------------------------------------------------------ the question
    add("## The question\n")
    add(_excerpt(goal.section("Question")) + "\n")
    add(_goal_meta(front) + "\n")
    criterion = goal.section("Success criterion")
    if criterion.strip():
        add("**Success criterion.** " + _excerpt(criterion, 600) + "\n")

    # ------------------------------------------------------------------ where it stands
    add("## Where it stands\n")
    reproducible = [r for r in runs if str(r.get("reproducible", "")).lower() == "true"]
    leader = best(reproducible, metric, direction) if metric and reproducible else None

    promoted = ""
    if project.leaderboard.is_file():
        text = project.leaderboard.read_text(encoding="utf-8")
        entries = [b for b in text.split("### ")[1:] if goal_id in b.splitlines()[0]]
        promoted = entries[-1] if entries else ""

    if promoted:
        add("A result has passed the promotion gate:\n")
        add("### " + promoted.rstrip() + "\n")
    elif leader:
        add(
            f"**Nothing has been promoted yet.** The best reproducible run so far is "
            f"`{leader.get('run_id')}` from `{leader.get('experiment_id')}` at "
            f"`{metric} = {leader.get(metric)}` — a candidate, not a result, until it passes the "
            f"gate (`hiraku-auto promote {leader.get('experiment_id')} "
            f"--run {leader.get('run_id')}`).\n"
        )
    elif runs:
        add(
            "**Nothing has been promoted, and no run is eligible.** Every run recorded under this "
            "goal is non-reproducible (`run_kind: worktree`), so none can be promoted. Re-run with "
            "`--commit HEAD`.\n"
        )
    else:
        add("**No runs have been recorded under this goal yet.**\n")

    counts = {
        "experiments": len(nodes),
        "concluded": sum(1 for n in nodes if n.status == "concluded"),
        "open": sum(1 for n in nodes if n.status in {"running", "approved"}),
        "proposed": sum(1 for n in nodes if n.status == "proposed"),
        "runs": len(runs),
        "reproducible runs": len(reproducible),
    }
    add(" · ".join(f"**{v}** {k}" for k, v in counts.items()) + "\n")

    # ------------------------------------------------------------------ the tree
    add("## The tree\n")
    add("```\n" + render_ascii(forest, goal_id).rstrip() + "\n```\n")

    # ------------------------------------------------------------------ what is known
    supports = [n for n in nodes if n.verdict == "supports"]
    add("## What is known\n")
    if not supports:
        add("_No experiment under this goal has had its expectation supported yet._\n")
    for node in supports:
        add(f"### {_node_line(forest, node.id)}\n")
        if node.front.get("expectation"):
            add(f"**Expectation.** {node.front['expectation']}\n")
        add(_excerpt(node.record.section("Conclusions")) + "\n")
        if node.runs:
            add(f"_Evidence: {len(node.runs)} run(s) — "
                + ", ".join(f"`{r.get('run_id')}`" for r in node.runs[:6])
                + ("…" if len(node.runs) > 6 else "")
                + "._\n")

    # ------------------------------------------------------------------ what was ruled out
    refuted = [n for n in nodes if n.verdict == "refutes"] + [
        n for n in nodes if n.status == "abandoned" and n.verdict != "refutes"
    ]
    add("## What was ruled out\n")
    if not refuted:
        add("_Nothing has been refuted or abandoned._\n")
    for node in refuted:
        add(f"### {_node_line(forest, node.id)}\n")
        if node.front.get("expectation"):
            add(f"**Expectation, refuted.** {node.front['expectation']}\n")
        add(_excerpt(node.record.section("Conclusions")) + "\n")

    # ------------------------------------------------------------------ what is open
    open_nodes = [n for n in nodes if n.status in {"running", "approved", "proposed"}]
    inconclusive = [n for n in nodes if n.verdict == "inconclusive"]
    add("## What is still open\n")
    if not open_nodes and not inconclusive:
        add(
            "_Nothing. Every node is concluded and nothing is proposed — this goal is exhausted "
            "unless a new question is opened._\n"
        )
    for node in inconclusive:
        add(f"### {_node_line(forest, node.id)} — inconclusive\n")
        add(_excerpt(node.record.section("Conclusions"), 800) + "\n")
    for node in open_nodes:
        add(f"### {_node_line(forest, node.id)}\n")
        if node.front.get("expectation"):
            add(f"**Expectation.** {node.front['expectation']}\n")
        decisions = node.record.section("Decisions")
        if decisions.strip():
            add(_excerpt(decisions, 700) + "\n")

    pending = [
        p for p in forest.proposals.values()
        if p.front.get("goal") == goal_id
        and str(p.front.get("status") or "proposed") == "proposed"
    ]
    if pending:
        add("### Proposals awaiting a decision\n")
        for record in pending:
            add(
                f"- `{record.front.get('id')}` ({record.front.get('origin')}) — "
                + (record.section("Hypothesis").strip().splitlines() or ["_no hypothesis_"])[0]
            )
        add("")

    threads = goal.section("Open threads")
    if threads.strip() and "parking area" not in threads:
        add("### Open threads recorded on the goal\n")
        add(_excerpt(threads, 700) + "\n")

    # ------------------------------------------------------------------ runs
    add("## Runs\n")
    if not runs:
        add("_None recorded._\n")
    else:
        ordered = rank(runs, metric, descending=(direction == "maximize")) if metric else runs
        columns = ["run_id", "experiment_id", "reproducible", "seed"]
        if metric:
            columns.append(metric)
        if front.get("gate_metric"):
            columns.append(str(front["gate_metric"]))
        columns += ["launched_at", "commit"]
        columns = [c for c in columns if any(r.get(c) not in (None, "") for r in ordered)]
        add("| " + " | ".join(columns) + " |")
        add("|" + "|".join("---" for _ in columns) + "|")
        for row in ordered[:60]:
            cells = []
            for column in columns:
                value = str(row.get(column, ""))
                if column == "commit":
                    value = value[:10]
                cells.append(value or "—")
            add("| " + " | ".join(cells) + " |")
        if len(ordered) > 60:
            add(f"\n_{len(ordered) - 60} further run(s) not shown — see `hiraku-auto runs`._")
        add("")

    # ------------------------------------------------------------------ gaps
    scoped = [i for i in forest.issues if i.split(":")[0] in set(node_ids)]
    add("## Gaps in the record\n")
    if not scoped:
        add("_None. Every record under this goal passes `hiraku-auto check`._\n")
    else:
        add("These weaken the report above, because the report can only quote what was written:\n")
        for issue in scoped:
            add(f"- {issue}")
        add("")

    return "\n".join(out).rstrip() + "\n"
