"""The implementation brief: what someone — or something — needs to write an experiment's code.

Separate from the CLI because two callers need the same text: `exp brief`, which prints it for a
human to hand over, and `agent implement`, which is that hand-over automated.
"""

from __future__ import annotations

from .config import Project
from .errors import HirakuError
from .records import unset_markers
from .tree import build


def render(project: Project, experiment_id: str) -> str:
    forest = build(project)
    if experiment_id not in forest.nodes:
        raise HirakuError(f"Experiment '{experiment_id}' does not exist.")
    node = forest.nodes[experiment_id]
    goal = forest.goals.get(str(node.goal))
    if goal is None:
        raise HirakuError(
            f"'{experiment_id}' has no goal, so there is no protocol to implement against."
        )

    metrics = [str(goal.front.get("primary_metric") or "")]
    if goal.front.get("gate_metric"):
        metrics.append(str(goal.front["gate_metric"]))
    named = ", ".join(f"`{m}`" for m in metrics if m)
    exp_dir = project.experiments_dir / experiment_id

    out: list[str] = [
        f"# Implementation brief — `{experiment_id}`",
        "",
        "You are writing the code for one experiment in a bounded research loop. The records "
        "quoted below are fixed: do not edit them, and do not adjust the protocol to suit the "
        "implementation. If the protocol cannot be implemented as written, say so and stop — that "
        "is a finding, not an obstacle to work around.",
        "",
        "## What this experiment claims",
        "",
        f"- **id** `{experiment_id}` · **kind** {node.kind} · **goal** `{node.goal}`",
        f"- **branch** `{node.front.get('branch')}` — the code belongs here, not on the default "
        f"branch",
        "",
        "**Expectation** — the sentence the run has to be able to refute:",
        "",
        f"> {str(node.front.get('expectation') or '**[none recorded]**')}",
        "",
        node.record.section("Scope").strip(),
        "",
        "## The run contract",
        "",
        f"`hiraku-auto launch {experiment_id}:<entrypoint>` runs your command with these "
        f"variables set. The row it records is the only evidence the promotion gate ever sees.",
        "",
        f"- `$HIRAKU_METRICS_PATH` — write one flat JSON object here. The keys must be metric "
        f"names "
        f"this goal defines: {named or '(none declared)'}, plus any diagnostic the protocol below "
        f"requires. A name the protocol does not define is not comparable to anything.",
        "- `$HIRAKU_SEED` — use it for every source of randomness. One run, one seed.",
        "- `$HIRAKU_RUN_ID`, `$HIRAKU_GIT_COMMIT`, `$HIRAKU_EXPERIMENT_ID`, "
        "`$HIRAKU_PROJECT_ROOT` — available for your own logging; the tool records them itself.",
        "- Exit non-zero on failure. Exiting zero with no metrics file is an execution defect and "
        "is recorded as one.",
        "",
        f"Declare the command in `{project.rel(exp_dir / 'launch.yml')}`:",
        "",
        "```yaml",
        "entrypoints:",
        "  main:",
        '    command: ["uv", "run", "python", "-m", "<your_package>.<module>", "--variant", '
        f'"{experiment_id}"]',
        "    cwd: .        # ../<code-repo> if the code lives in a sibling repository",
        "```",
        "",
        "## The goal's frozen protocol — implement against this exactly",
        "",
        f"Primary metric `{goal.front.get('primary_metric')}` ({goal.front.get('direction')}"
        + (f", target {goal.front['target']}" if goal.front.get("target") is not None else "")
        + ")"
        + (
            f"; gate metric `{goal.front['gate_metric']}` must not degrade."
            if len(metrics) > 1
            else "."
        ),
        "",
        goal.section("Evaluation protocol").strip(),
        "",
        "## Out of scope for this goal",
        "",
        goal.section("Out of scope").strip() or "(nothing recorded)",
    ]
    holes = unset_markers(goal) + unset_markers(node.record)
    if holes:
        out += [
            "",
            "## Unfilled values — ask, never invent",
            "",
            "These are marked unset in the records. Do not pick a value to make the code run; "
            "report which one you need and stop.",
            "",
        ]
        out += [f"- **{section}** — {needed}" for section, needed in holes]

    return "\n".join(out) + "\n"
