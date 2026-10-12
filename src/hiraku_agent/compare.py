"""Deterministic metric tables for `analyze`: the numbers, computed in code.

An agent is good at reading a comparison and bad at transcribing one: a mean copied
wrongly out of a run table reads exactly like a measured value. So every number in
`analyze`'s output — a node's means and spreads, its difference from a baseline, a
task-wide leaderboard — is computed here, from the run rows, and written into the record
by the tool. The agent is handed the finished table and asked to *interpret* it,
never to produce one.

Nothing here decides anything. "Better" and "tied" depend on a tolerance that belongs to
the task's `## Decision rule`, not to this module, so tables report differences
(`delta`, in the metric's own units, and `improvement`, the same difference signed so
that positive always means better in the declared direction) and leave judging them to
the rule.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MetricStat:
    """One metric over one node's runs: how many, the mean, the sample standard
    deviation (`None` below two runs — a spread needs at least two), and the values."""

    n: int
    mean: float | None
    std: float | None
    values: tuple[float, ...]

    def to_state(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "mean": self.mean,
            "std": self.std,
            "values": list(self.values),
        }


def declared_metrics(task: Any) -> list[tuple[str, str]]:
    """`(name, direction)` for each metric the task declares, in declared order."""
    declared: list[tuple[str, str]] = []
    for item in getattr(task, "metrics", None) or []:
        name = getattr(item, "name", None) or (
            item.get("name") if isinstance(item, Mapping) else None
        )
        direction = getattr(item, "direction", None) or (
            item.get("direction") if isinstance(item, Mapping) else None
        )
        if name and direction:
            declared.append((str(name), str(direction)))
    return declared


def _numbers(runs: Sequence[Any], name: str) -> tuple[float, ...]:
    values: list[float] = []
    for run in runs:
        raw = (getattr(run, "metrics", None) or {}).get(name)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        if math.isnan(raw):
            continue
        values.append(float(raw))
    return tuple(values)


def metric_stat(runs: Sequence[Any], name: str) -> MetricStat:
    values = _numbers(runs, name)
    if not values:
        return MetricStat(0, None, None, ())
    mean = sum(values) / len(values)
    std = None
    if len(values) > 1:
        std = math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))
    return MetricStat(len(values), mean, std, values)


def node_stats(
    runs: Sequence[Any], metrics: Sequence[tuple[str, str]]
) -> dict[str, MetricStat]:
    return {name: metric_stat(runs, name) for name, _ in metrics}


def improvement(delta: float, direction: str) -> float:
    """`delta` signed so that positive means better in `direction`."""
    return delta if direction == "maximize" else -delta


# ------------------------------------------------------------------ node vs baseline


@dataclass(frozen=True)
class Row:
    """One metric of one node, set against one baseline."""

    metric: str
    direction: str
    baseline: MetricStat
    candidate: MetricStat
    delta: float | None
    improvement: float | None
    improvement_in_baseline_std: float | None

    def to_state(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "direction": self.direction,
            "baseline": self.baseline.to_state(),
            "candidate": self.candidate.to_state(),
            "delta": self.delta,
            "improvement": self.improvement,
            "improvement_in_baseline_std": self.improvement_in_baseline_std,
        }


@dataclass(frozen=True)
class Comparison:
    """A node's metrics against each baseline node that has runs."""

    node: str
    baselines: dict[str, list[Row]]

    def __bool__(self) -> bool:
        return any(self.baselines.values())

    def to_state(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "baselines": {
                key: [row.to_state() for row in rows]
                for key, rows in self.baselines.items()
            },
        }


def _row(name: str, direction: str, baseline: MetricStat, candidate: MetricStat) -> Row:
    if baseline.mean is None or candidate.mean is None:
        return Row(name, direction, baseline, candidate, None, None, None)
    delta = candidate.mean - baseline.mean
    gain = improvement(delta, direction)
    scaled = gain / baseline.std if baseline.std else None
    return Row(name, direction, baseline, candidate, delta, gain, scaled)


def compare_to_baselines(
    task: Any,
    nodes: Sequence[Any],
    runs_by_id: Mapping[str, Sequence[Any]],
    *,
    focus: str,
) -> Comparison:
    """`focus`'s metrics against every other node of `kind == "baseline"` that has runs.

    Empty (falsy) when the task declares no metrics, `focus` has no runs, or no
    baseline has any — there is then nothing to compare against, and `analyze` says so
    rather than inventing a reference.
    """
    metrics = declared_metrics(task)
    candidate_stats = node_stats(runs_by_id.get(focus, ()), metrics)
    result: dict[str, list[Row]] = {}
    for node in nodes:
        if node.id == focus or getattr(node, "kind", None) != "baseline":
            continue
        runs = runs_by_id.get(node.id, ())
        if not runs:
            continue
        baseline_stats = node_stats(runs, metrics)
        result[node.id] = [
            _row(name, direction, baseline_stats[name], candidate_stats[name])
            for name, direction in metrics
        ]
    return Comparison(focus, result)


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.4g}"


def _cell(stat: MetricStat) -> str:
    if stat.mean is None:
        return "—"
    spread = f" ± {stat.std:.2g}" if stat.std is not None else ""
    return f"{_fmt(stat.mean)}{spread} (n={stat.n})"


def comparison_markdown(comparison: Comparison) -> str:
    """The `## Metric comparison` body for a node: one table per baseline."""
    blocks: list[str] = [
        "_Computed from the run table by the tool, not by the agent. `improvement` is "
        "the difference signed so that positive is better in the declared direction; "
        "`/ baseline std` needs at least two baseline runs._"
    ]
    for baseline_id, rows in comparison.baselines.items():
        lines = [
            f"**vs `{baseline_id}`**",
            "",
            "| metric | direction | baseline | this node | delta | improvement "
            "| / baseline std |",
            "|---|---|---|---|---|---|---|",
        ]
        for row in rows:
            lines.append(
                f"| `{row.metric}` | {row.direction} | {_cell(row.baseline)} "
                f"| {_cell(row.candidate)} | {_fmt(row.delta)} "
                f"| {_fmt(row.improvement)} | {_fmt(row.improvement_in_baseline_std)} |"
            )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# -------------------------------------------------------------------- task leaderboard


@dataclass(frozen=True)
class Leaderboard:
    """Every node that has runs, side by side, for one task."""

    metrics: tuple[tuple[str, str], ...]
    stats: dict[str, dict[str, MetricStat]]
    pending: tuple[str, ...]
    ranking: dict[str, list[str]]
    pareto: tuple[str, ...]
    pareto_metrics: tuple[str, ...]

    @property
    def has_numbers(self) -> bool:
        """Whether any declared metric has at least one numeric value on any node."""
        return any(
            stat.n for per_node in self.stats.values() for stat in per_node.values()
        )

    def to_state(self) -> dict[str, Any]:
        return {
            "metrics": [{"name": n, "direction": d} for n, d in self.metrics],
            "per_node": {
                node: {name: stat.to_state() for name, stat in stats.items()}
                for node, stats in self.stats.items()
            },
            "ranking_by_metric": self.ranking,
            "not_dominated": list(self.pareto),
            "not_dominated_over": list(self.pareto_metrics),
            "pending_nodes": list(self.pending),
        }


def build_leaderboard(
    task: Any,
    nodes: Sequence[Any],
    runs_by_id: Mapping[str, Sequence[Any]],
) -> Leaderboard:
    metrics = tuple(declared_metrics(task))
    stats = {
        node.id: node_stats(runs_by_id[node.id], metrics)
        for node in nodes
        if runs_by_id.get(node.id)
    }
    pending = tuple(node.id for node in nodes if not runs_by_id.get(node.id))
    ranking: dict[str, list[str]] = {}
    for name, direction in metrics:
        scored = [
            (node, stat[name].mean)
            for node, stat in stats.items()
            if stat[name].mean is not None
        ]
        scored.sort(key=lambda item: -improvement(item[1], direction))
        ranking[name] = [node for node, _ in scored]
    common = tuple(
        name
        for name, _ in metrics
        if stats and all(stat[name].mean is not None for stat in stats.values())
    )
    return Leaderboard(
        metrics,
        stats,
        pending,
        ranking,
        _not_dominated(stats, metrics, common),
        common,
    )


def _not_dominated(
    stats: Mapping[str, Mapping[str, MetricStat]],
    metrics: Sequence[tuple[str, str]],
    common: Sequence[str],
) -> tuple[str, ...]:
    """Nodes no other node beats-or-ties on every common metric and beats on one.

    Reported over *all* common metrics, so it is a conservative filter ("who is not
    strictly worse than someone else everywhere"), not a verdict: whether a metric
    matters is for the task's decision rule.
    """
    direction = dict(metrics)
    vectors = {
        node: [improvement(stat[name].mean or 0.0, direction[name]) for name in common]
        for node, stat in stats.items()
    }
    front: list[str] = []
    for node, vector in vectors.items():
        dominated = any(
            other != node
            and all(o >= v for o, v in zip(vectors[other], vector, strict=True))
            and any(o > v for o, v in zip(vectors[other], vector, strict=True))
            for other in vectors
        )
        if common and not dominated:
            front.append(node)
    return tuple(front)


def leaderboard_markdown(board: Leaderboard) -> str:
    """The `## Metric comparison` body for a task: one row per node, one column per
    metric, plus the nodes that have no runs yet."""
    lines: list[str] = []
    if board.pending:
        lines += [
            "**Partial.** No runs yet for: "
            + ", ".join(f"`{node}`" for node in board.pending)
            + ". Re-run `hiraku-agent analyze` on the task once they have.",
            "",
        ]
    lines.append(
        "_Computed from the run table by the tool, not by the agent. Cells are "
        "mean ± sample std (n runs); the arrow shows the declared direction._"
    )
    lines.append("")
    header = ["node"] + [
        f"`{name}` {'↑' if direction == 'maximize' else '↓'}"
        for name, direction in board.metrics
    ]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * len(header))
    for node, stat in board.stats.items():
        mark = " *" if node in board.pareto else ""
        cells = [f"`{node}`{mark}"] + [_cell(stat[name]) for name, _ in board.metrics]
        lines.append("| " + " | ".join(cells) + " |")
    if board.pareto:
        lines += [
            "",
            "`*` not dominated: no other node is at least as good on every metric "
            f"({', '.join(board.pareto_metrics)}) and better on one.",
        ]
    return "\n".join(lines)
