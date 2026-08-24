"""The promotion gate.

Five conditions, checked in code rather than left to judgement, because a leaderboard you can talk
your way onto is not evidence. Condition 3 is the interesting one: it compares the goal record as it
was at the run's commit against the goal record now, so silently changing the evaluation protocol to
win is caught rather than trusted.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Any

from . import gitops
from .config import Project
from .errors import HirakuError
from .records import load_goal, read_record
from .runs import best
from .scaffold import today
from .tree import build


@dataclass
class Condition:
    name: str
    passed: bool
    detail: str


@dataclass
class GateResult:
    experiment: str
    run_id: str
    conditions: list[Condition] = field(default_factory=list)
    metric_value: float | None = None
    incumbent: dict[str, str] | None = None
    reproduction: dict[str, str] | None = None

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.conditions)

    def failures(self) -> list[Condition]:
        return [c for c in self.conditions if not c.passed]


def _number(row: dict[str, str], key: str) -> float | None:
    try:
        return float(row.get(key, ""))
    except (TypeError, ValueError):
        return None


def _better(new: float, old: float, direction: str) -> bool:
    return new < old if direction == "minimize" else new > old


def _goal_blob_at(project: Project, commit: str, goal_rel: str) -> str | None:
    if not commit or not gitops.is_repo(project.root):
        return None
    result = subprocess.run(
        ["git", "show", f"{commit}:{goal_rel}"],
        cwd=str(project.root),
        capture_output=True,
        text=True,
    )
    return result.stdout if result.returncode == 0 else None


def evaluate(
    project: Project,
    experiment_id: str,
    run_id: str,
    *,
    signed_off_by: str | None = None,
) -> GateResult:
    forest = build(project)
    if experiment_id not in forest.nodes:
        raise HirakuError(f"Experiment '{experiment_id}' does not exist.")
    node = forest.nodes[experiment_id]
    if not node.goal:
        raise HirakuError(f"'{experiment_id}' has no goal, so there is nothing to promote it to.")

    goal = load_goal(project, node.goal)
    metric = str(goal.front.get("primary_metric") or "")
    direction = str(goal.front.get("direction") or "minimize")
    gate_metric = str(goal.front.get("gate_metric") or "")
    if not metric:
        raise HirakuError(f"Goal '{node.goal}' has no primary_metric.")

    rows = [r for r in node.runs if r.get("run_id") == run_id]
    if not rows:
        raise HirakuError(f"Run '{run_id}' not found for '{experiment_id}'.")
    row = rows[0]

    promotion = {}
    if project.config_md.is_file():
        promotion = read_record(project.config_md).front.get("promotion") or {}
    tolerance = float(promotion.get("agreement_tolerance") or 0.0)
    needed_seeds = int(promotion.get("reproduction_seeds") or 1)

    result = GateResult(experiment=experiment_id, run_id=run_id)
    value = _number(row, metric)
    result.metric_value = value

    # 1 — better on the goal's terms, primary metric and gate metric together.
    all_rows = [r for r in forest.table.rows if r.get("run_id") != run_id]
    incumbent = _current_leader(project, forest, node.goal, metric, direction, all_rows)
    result.incumbent = incumbent
    if value is None:
        result.conditions.append(
            Condition("better", False, f"run has no numeric '{metric}'")
        )
    elif incumbent is None:
        result.conditions.append(
            Condition("better", True, f"{metric}={value}; no incumbent for this goal")
        )
    else:
        old = _number(incumbent, metric)
        if old is None:
            result.conditions.append(
                Condition("better", True, f"{metric}={value}; incumbent has no comparable value")
            )
        elif _better(value, old, direction):
            result.conditions.append(
                Condition(
                    "better",
                    True,
                    f"{metric} {value} beats {old} ({incumbent.get('experiment_id')}, "
                    f"{direction})",
                )
            )
        else:
            result.conditions.append(
                Condition("better", False, f"{metric} {value} does not beat {old} ({direction})")
            )

    if gate_metric:
        new_gate = _number(row, gate_metric)
        old_gate = _number(incumbent or {}, gate_metric)
        if new_gate is None:
            result.conditions.append(
                Condition("gate", False, f"run has no numeric '{gate_metric}'")
            )
        elif old_gate is None:
            result.conditions.append(
                Condition("gate", True, f"{gate_metric}={new_gate}; no incumbent value to compare")
            )
        else:
            # The gate must not degrade. Its own direction is the opposite of the primary metric's
            # only by coincidence, so treat "no worse" as within tolerance of the incumbent.
            degraded = (
                new_gate < old_gate - tolerance
                if direction == "minimize"
                else new_gate < old_gate - tolerance
            )
            result.conditions.append(
                Condition(
                    "gate",
                    not degraded,
                    f"{gate_metric} {new_gate} vs incumbent {old_gate}"
                    + ("" if not degraded else " — degraded beyond tolerance"),
                )
            )

    # 2 — reproducible.
    reproducible = str(row.get("reproducible", "")).lower() == "true"
    result.conditions.append(
        Condition(
            "reproducible",
            reproducible,
            "commit-backed run"
            if reproducible
            else f"run_kind={row.get('run_kind') or '?'}: a working-tree run is never promotable",
        )
    )

    # 3 — the goal's evaluation protocol is the one the run was measured under.
    goal_rel = project.rel(goal.path)
    blob = _goal_blob_at(project, str(row.get("commit") or ""), goal_rel)
    if blob is None:
        result.conditions.append(
            Condition(
                "protocol",
                False,
                f"could not read {goal_rel} at commit {str(row.get('commit'))[:10]} — commit the "
                f"goal record so the protocol is verifiable",
            )
        )
    elif blob == goal.path.read_text(encoding="utf-8"):
        result.conditions.append(
            Condition("protocol", True, "goal record unchanged since the run's commit")
        )
    else:
        result.conditions.append(
            Condition(
                "protocol",
                False,
                f"{goal_rel} changed since the run's commit — the result may not be comparable "
                f"(`git diff {str(row.get('commit'))[:10]} -- {goal_rel}`)",
            )
        )

    # 4 — independently reproduced at another seed on the same commit.
    siblings = [
        r
        for r in node.runs
        if r.get("run_id") != run_id
        and r.get("commit") == row.get("commit")
        and str(r.get("reproducible", "")).lower() == "true"
        and r.get("seed") not in ("", row.get("seed"))
        and _number(r, metric) is not None
    ]
    agreeing = [
        r
        for r in siblings
        if value is not None and abs((_number(r, metric) or 0.0) - value) <= tolerance
    ]
    if len(agreeing) >= needed_seeds:
        result.reproduction = agreeing[0]
        result.conditions.append(
            Condition(
                "reproduced",
                True,
                f"{agreeing[0].get('run_id')} at seed {agreeing[0].get('seed')}: "
                f"{metric}={_number(agreeing[0], metric)} (within {tolerance})",
            )
        )
    else:
        detail = f"need {needed_seeds} agreeing run(s) at another seed within {tolerance}"
        if siblings:
            spread = [f"{r.get('run_id')}={_number(r, metric)}" for r in siblings]
            detail += (
                f"; found {len(siblings)} other seed(s) but outside tolerance: "
                + ", ".join(spread)
            )
        result.conditions.append(Condition("reproduced", False, detail))

    # 5 — sign-off.
    mode = str(node.front.get("mode") or "guided")
    if signed_off_by:
        result.conditions.append(Condition("signed_off", True, f"by {signed_off_by}"))
    elif mode == "auto" and result.reproduction is not None:
        result.conditions.append(
            Condition("signed_off", True, "auto mode, satisfied by the independent reproduction")
        )
    else:
        result.conditions.append(
            Condition(
                "signed_off",
                False,
                "guided mode needs --signed-off-by NAME"
                if mode == "guided"
                else "auto mode needs the reproduction in condition 4",
            )
        )
    return result


def _current_leader(
    project: Project,
    forest: Any,
    goal_id: str,
    metric: str,
    direction: str,
    rows: list[dict[str, str]],
) -> dict[str, str] | None:
    """Best promotable run already recorded under this goal.

    Read from the run table rather than parsed out of leaderboard.md — the table is data, the
    leaderboard is prose, and only one of those is safe to compare numbers from.
    """
    goal_nodes = set(forest.subtree_all(goal_id))
    candidates = [
        r
        for r in rows
        if r.get("experiment_id") in goal_nodes
        and str(r.get("reproducible", "")).lower() == "true"
    ]
    return best(candidates, metric, direction)


def render_entry(project: Project, result: GateResult, signed_off_by: str | None) -> str:
    forest = build(project)
    node = forest.nodes[result.experiment]
    goal = load_goal(project, str(node.goal))
    metric = str(goal.front.get("primary_metric"))
    row = next(r for r in node.runs if r.get("run_id") == result.run_id)
    reproduction = result.reproduction or {}
    lines = [
        "",
        f"### {node.goal} — `{result.experiment}` ({today()})",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Experiment | `{result.experiment}` |",
        f"| Metric | `{metric} = {result.metric_value}` |",
        f"| Run | `{result.run_id}` |",
        f"| Commit | `{row.get('commit')}` |",
        f"| Reproduction | `{reproduction.get('run_id', '—')}` at seed "
        f"{reproduction.get('seed', '—')} |",
        f"| Signed off by | {signed_off_by or 'auto + reproduction'} |",
        "",
        "Gate:",
        "",
    ]
    lines += [f"- `{c.name}` — {c.detail}" for c in result.conditions]
    lines.append("")
    return "\n".join(lines)


def promote(
    project: Project,
    experiment_id: str,
    run_id: str,
    *,
    signed_off_by: str | None = None,
    force: bool = False,
) -> GateResult:
    result = evaluate(project, experiment_id, run_id, signed_off_by=signed_off_by)
    if not result.passed and not force:
        return result
    path = project.leaderboard
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "" if path.is_file() else "# Leaderboard\n\nPromoted results only. Append-only.\n"
    with path.open("a", encoding="utf-8") as stream:
        if header:
            stream.write(header)
        if force and not result.passed:
            stream.write(
                "\n> Forced promotion — the gate did not pass. Failed conditions: "
                + ", ".join(c.name for c in result.failures())
                + "\n"
            )
        stream.write(render_entry(project, result, signed_off_by))
    return result
