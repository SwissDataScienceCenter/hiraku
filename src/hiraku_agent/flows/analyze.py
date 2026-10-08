"""`analyze`: judge a node's runs, or read a whole task's nodes together."""

from __future__ import annotations

from typing import Any

from hiraku.config import HirakuConfig
from hiraku.errors import HirakuError
from hiraku.record import Record, load_record

from .. import compare, sections
from ..engine import AgentRun, Hooks
from ..lock import writes
from ..node import (
    Node,
    cached_runs,
    list_nodes,
    load_node,
    task_of,
)
from ..replies import AnalyzeReply
from ._common import AgentCall, _node_bookkeeping, task_state
from .discover import INVESTIGATION_HEADING


@writes("analyze")
def run_analyze(
    node_id: str,
    *,
    task_id: str | None = None,
    only: list[str] | None = None,
    instruction: str = "",
    command: list[str] | None = None,
    dry_run: bool = False,
    apply: bool = True,
    context_paths: list[str] | None = None,
    repo_paths: list[str] | None = None,
    context_bytes: int | None = None,
    unset_env: list[str] | None = None,
    hooks: Hooks | None = None,
    timeout: int | None = None,
    action: str | None = None,
    context_id: str | None = None,
    allow_running: bool = False,
    config: HirakuConfig | None = None,
) -> AgentRun:
    """Judge a node's runs against its expectation and write its findings — or, given a
    *task* id, compare every node under the task and answer its question.

    Any record id works, resolved from the record itself, the way `run_update` does: a
    root record (`parent_id is None`) is a task and gets `_analyze_task`; anything else
    is a node and is judged here.

    A node's file gets a `## Metric comparison` table (computed by
    `hiraku_agent.compare`, not by the agent), `## Findings`, `## Conclusions`,
    `## Decisions and next steps` and `## Missing evidence`; its `verdict` is set when
    the agent returns one.

    A node whose launch is still in flight is refused (its runs are partial) unless
    `allow_running`, in which case the findings say they are based on partial results.
    Runs `triage` marked as an execution defect (`defect_runs`) are left out of the
    comparison table and the agent is told not to use them. The run ids judged are
    recorded in `analyzed_runs`, so an analysis that reached no verdict is not repeated
    until new runs arrive.
    """
    selected = config or HirakuConfig.discover()
    record = load_record(node_id, config=selected)
    if only and record.parent_id is not None:
        raise HirakuError("--nodes applies to a task: pass a task id, not a node id.")
    if record.parent_id is None:
        return _analyze_task(
            record,
            only=only,
            instruction=instruction,
            command=command,
            dry_run=dry_run,
            apply=apply,
            context_paths=context_paths,
            repo_paths=repo_paths,
            context_bytes=context_bytes,
            unset_env=unset_env,
            hooks=hooks,
            timeout=timeout,
            action=action,
            context_id=context_id,
            config=selected,
        )
    node = load_node(node_id, task=task_id, config=selected)
    # One read of the tracking source for everything below, up to the agent call.
    with cached_runs():
        task, nodes, state, comparison, runs_by_id, partial = _node_analysis_inputs(
            node, allow_running=allow_running, config=selected
        )
    judged = sorted(str(run.id) for run in runs_by_id[node.id])
    call = AgentCall.resolve(
        selected,
        entity=task.id,
        action=action,
        context_id=context_id,
        command=command,
        timeout=timeout,
        context_paths=context_paths,
        context_bytes=context_bytes,
        repo_paths=repo_paths,
        hooks=hooks,
        unset_env=unset_env,
    )
    result = call.run(
        "analyze",
        node.id,
        state,
        instruction=instruction,
        dry_run=dry_run,
        reply=AnalyzeReply,
    )
    if dry_run or not apply:
        return result

    live = load_node(node.id, task=task, config=selected)
    markdown = live.markdown
    if comparison:
        markdown = sections.set_section(
            markdown, "Metric comparison", compare.comparison_markdown(comparison)
        )
    markdown = _write_analysis_sections(markdown, result.data)
    if partial:
        findings = sections.read_section(markdown, "Findings") or ""
        markdown = sections.set_section(
            markdown,
            "Findings",
            f"{PARTIAL_NOTE}\n\n{findings}".rstrip(),
        )
    verdict = result.data.get("verdict")
    if verdict in {"supports", "refutes", "inconclusive"}:
        live.verdict = verdict
        # A fresh verdict needs a fresh go/no-go decision — don't let a `triaged`
        # flag earned by a *previous* verdict make this node look already decided.
        live.triaged = False
        # New runs were judged: an earlier execution defect has been dealt with.
        live.execution_defect = False
    live.analyzed_runs = judged
    candidate = result.data.get("promotable_candidate")
    if candidate:
        markdown = sections.append_section(
            markdown,
            "Decisions and next steps",
            f"Candidate run: `{candidate}` — worth a person looking at as a possible "
            f"new reference.",
        )
    live.markdown = markdown
    live.save()
    result.written.append(live.path)
    return result


def _node_analysis_inputs(
    node: Node, *, allow_running: bool, config: HirakuConfig
) -> tuple[Record, list[Node], dict[str, Any], Any, dict[str, list], bool]:
    """Everything a node-level analysis reads before calling the agent: the task, its
    nodes, the prompt state, the computed comparison, the runs being judged (without
    the ones `triage` marked defective), and whether the launch is still running."""
    if not node.runs(config=config):
        raise HirakuError(f"'{node.id}' has no runs to analyse.")
    task = task_of(node, config=config)
    nodes = list_nodes(task=task, config=config)
    runs_by_id, states, _ = _node_bookkeeping(nodes, config=config)
    partial = states.get(node.id) == "running"
    if partial and not allow_running:
        raise HirakuError(
            f"'{node.id}' is still running, so its runs are partial. Wait for the "
            "launch to end, or pass --allow-running to analyse what is there so far."
        )
    defective = set(node.defect_runs or [])
    if defective:
        runs_by_id[node.id] = [
            run for run in runs_by_id[node.id] if str(run.id) not in defective
        ]
    comparison = compare.compare_to_baselines(task, nodes, runs_by_id, focus=node.id)
    state = task_state(task, nodes, config=config, focus=node.id)
    if comparison:
        state["metric_comparison"] = comparison.to_state()
    return task, nodes, state, comparison, runs_by_id, partial


PARTIAL_NOTE = "_Partial results: the launch was still running when this was analysed._"


_ANALYSIS_SECTIONS = (
    ("findings", "Findings"),
    ("conclusions", "Conclusions"),
    ("next_steps", "Decisions and next steps"),
)
_TASK_ANALYSIS_SECTIONS = (
    ("findings", "Findings"),
    ("insights", "Cross-node insights"),
    ("conclusions", "Conclusions"),
    ("next_steps", "Decisions and next steps"),
)


def _write_analysis_sections(
    markdown: str,
    data: dict[str, Any],
    plan: tuple[tuple[str, str], ...] = _ANALYSIS_SECTIONS,
) -> str:
    """The prose sections an `analyze` reply carries, plus `## Missing evidence`.

    `missing_evidence` used to be printed to the terminal and lost; it is a section of
    its own, rather than a line appended to `## Decisions and next steps`, so a re-run
    replaces it instead of stacking a second copy. An agent that now reports nothing
    missing clears a stale one.
    """
    for key, heading in plan:
        if data.get(key):
            markdown = sections.set_section(markdown, heading, str(data[key]))
    missing = data.get("missing_evidence")
    if missing:
        markdown = sections.set_section(markdown, "Missing evidence", str(missing))
    elif sections.read_section(markdown, "Missing evidence"):
        markdown = sections.set_section(
            markdown, "Missing evidence", "None identified in the latest analysis."
        )
    return markdown


def _excerpt(markdown: str, heading: str, limit: int) -> str:
    return sections.read_section(markdown, heading)[:limit]


def _analyze_task(
    task: Record,
    *,
    only: list[str] | None,
    instruction: str,
    command: list[str] | None,
    dry_run: bool,
    apply: bool,
    context_paths: list[str] | None,
    repo_paths: list[str] | None,
    context_bytes: int | None,
    unset_env: list[str] | None,
    hooks: Hooks | None,
    timeout: int | None,
    action: str | None,
    context_id: str | None,
    config: HirakuConfig,
) -> AgentRun:
    """Task-level `analyze`: what the task's nodes show, separately and together —
    written into the task's own file, not a separate record.

    Deliberately not a leaderboard. A benchmark wants one (and gets a computed table
    when the declared metrics have numbers), but the nodes of a task are often parallel
    and independent and still say something jointly — a diagnostic that explains a
    method's failure, two results that contradict each other — and some nodes are
    investigations that produce findings rather than runs. So the evidence is any node's
    runs *or* its written `## Findings`/`## Conclusions`, and the agent is asked for
    per-node findings, cross-node insights and a conclusion, with untested inference
    labelled as such.

    A task's *plan* sections (goal, question, metrics, `## Proposed protocol`,
    `## Decision rule`) are frozen once experiments exist, but its *result* sections are
    not: this is the same split a node already has, where `analyze` keeps writing
    `## Findings` and `## Conclusions` into a node long after its plan is fixed. It sets
    no verdict — a task has no expectation to judge — and re-running it replaces the
    result sections, which is how a partial answer becomes a final one.
    """
    all_nodes = list_nodes(task=task, config=config)
    nodes, left_out = _select_nodes(all_nodes, only)
    runs_by_id, states, _ = _node_bookkeeping(nodes, config=config)
    has_evidence = any(
        runs_by_id[node.id]
        or sections.read_section(node.markdown, "Findings")
        or sections.read_section(node.markdown, "Conclusions")
        or sections.read_section(node.markdown, INVESTIGATION_HEADING)
        for node in nodes
    )
    if not has_evidence:
        raise HirakuError(
            f"Task '{task.id}' has no evidence to analyse yet: no node"
            f"{' of those selected' if left_out else ''} has runs or a written "
            f"Findings, Conclusions or Investigation section."
        )
    board = compare.build_leaderboard(task, nodes, runs_by_id)
    call = AgentCall.resolve(
        config,
        entity=task.id,
        action=action,
        context_id=context_id,
        command=command,
        timeout=timeout,
        context_paths=context_paths,
        context_bytes=context_bytes,
        repo_paths=repo_paths,
        hooks=hooks,
        unset_env=unset_env,
    )
    state: dict[str, Any] = {
        "task": task.model_dump(mode="json", exclude={"path"}),
        "nodes": [
            {
                "id": node.id,
                "kind": node.kind,
                "parent_id": node.parent_id,
                "state": states[node.id],
                "verdict": node.verdict,
                "expectation": node.expectation,
                "n_runs": len(runs_by_id[node.id]),
                "scope": _excerpt(node.markdown, "Scope", 800),
                "findings": _excerpt(node.markdown, "Findings", 2000),
                "conclusions": _excerpt(node.markdown, "Conclusions", 1500),
                "missing_evidence": _excerpt(node.markdown, "Missing evidence", 500),
                "investigation": _excerpt(node.markdown, INVESTIGATION_HEADING, 2500),
            }
            for node in nodes
        ],
    }
    if left_out:
        state["scope"] = {
            "analysed_only": [node.id for node in nodes],
            "not_part_of_this_analysis": left_out,
        }
    if board.has_numbers:
        state["metric_table"] = board.to_state()
    result = call.run(
        "analyze_task",
        task.id,
        state,
        instruction=instruction,
        dry_run=dry_run,
    )
    if dry_run or not apply:
        return result

    live = load_record(task.id, config=config)
    if left_out:
        live.markdown = _write_subset_analysis(
            live.markdown, nodes, left_out, board, result.data
        )
    else:
        markdown = live.markdown
        if board.has_numbers:
            markdown = sections.set_section(
                markdown, "Metric comparison", compare.leaderboard_markdown(board)
            )
        live.markdown = _write_analysis_sections(
            markdown, result.data, _TASK_ANALYSIS_SECTIONS
        )
    live.save()
    result.written.append(live.path)
    return result


def _select_nodes(
    all_nodes: list[Node], only: list[str] | None
) -> tuple[list[Node], list[str]]:
    """The nodes a task-level analysis covers, and the ids it leaves out.

    No selection, or one naming every node, means the whole task (nothing left out).
    An id that is not a node of this task is an error rather than silently dropped: a
    typo would otherwise produce an analysis of fewer nodes than the researcher meant.
    """
    if not only:
        return all_nodes, []
    wanted = [item.strip() for item in only if item and item.strip()]
    known = {node.id for node in all_nodes}
    unknown = sorted(set(wanted) - known)
    if unknown:
        raise HirakuError(
            f"Not nodes of this task: {', '.join(unknown)}. "
            f"Nodes: {', '.join(sorted(known))}."
        )
    chosen = [node for node in all_nodes if node.id in set(wanted)]
    left_out = [node.id for node in all_nodes if node.id not in set(wanted)]
    return chosen, left_out


def _write_subset_analysis(
    markdown: str,
    nodes: list[Node],
    left_out: list[str],
    board: compare.Leaderboard,
    data: dict[str, Any],
) -> str:
    """A subset analysis goes in a section of its own, named for the nodes it covers.

    It must not replace the whole-task `## Findings`/`## Conclusions`: those answer the
    task's question over *all* its nodes, and an analysis of two of them is not that.
    The section says up front which nodes it covers and which it does not; the same
    subset re-run replaces its own section, a different subset gets another.
    """
    ids = [node.id for node in nodes]
    blocks = [
        f"**Analysed only: {', '.join(f'`{i}`' for i in ids)}.** "
        f"{len(left_out)} other node(s) of this task were not part of this analysis "
        f"({', '.join(f'`{i}`' for i in left_out)}), so nothing here is a whole-task "
        f"conclusion."
    ]
    if board.has_numbers:
        table = compare.leaderboard_markdown(board)
        blocks.append(f"### Metric comparison\n\n{table}")
    for key, heading in _TASK_ANALYSIS_SECTIONS:
        if data.get(key):
            blocks.append(f"### {heading}\n\n{str(data[key]).strip()}")
    missing = data.get("missing_evidence")
    if missing:
        blocks.append(f"### Missing evidence\n\n{str(missing).strip()}")
    return sections.set_section(
        markdown, f"Subset analysis: {', '.join(ids)}", "\n\n".join(blocks)
    )
