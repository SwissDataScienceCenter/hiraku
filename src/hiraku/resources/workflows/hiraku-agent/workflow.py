"""Hiraku agent: grow and settle a task's tree of experiments, one step at a time.

A port of the `hiraku-agent` workflows (SwissDataScienceCenter/hiraku#65) to Hiraku
workflows. Each step the scheduler picks (draft, design, discover, approve,
implement, launch, analyze, triage, fix) becomes one instruction to the coding
agent in the current conversation; its reply carries the step's output contract,
and this workflow writes the records exactly as `hiraku-agent` did.

A node's lifecycle is derived from evidence, never stored: proposed (no commit),
implemented (commit, no runs), running (a run reports it is still running),
awaiting analysis (runs, no verdict), concluded (verdict, not yet triaged), and
completed (triaged, has children, or abandoned). Approval is a decision, not a
state: `guided` nodes need a person, `auto` nodes clear it by themselves.
"""

from __future__ import annotations

import json
import math
import posixpath
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from hiraku.config import HirakuConfig
from hiraku.errors import GitError, HirakuError, NotReady, SubmissionRejected
from hiraku.git import GitRepository
from hiraku.io import json_value
from hiraku.launch import render_arguments
from hiraku.record import Entrypoint, Record, list_records, load_record
from hiraku.run import Run
from hiraku.validation import validate_identifier
from hiraku.workflow import Completion, Instruction, Session, Workflow, tool

Kind = Literal["baseline", "hypothesis", "diagnostic", "ablation", "combination"]
Origin = Literal["researcher", "agent-diagnostic", "agent-literature"]
Mode = Literal["guided", "auto"]
Verdict = Literal["supports", "refutes", "inconclusive"]

RUNNING_STATUSES = frozenset({"running", "scheduled", "pending"})
MAX_RUN_AGE = timedelta(days=7)
STATE_BUDGET_CHARS = 60_000

# ---------------------------------------------------------------------------- nodes


class Node(Record):
    """A record in the tree, with the fields the scheduler reads.

    The fields are ordinary record front matter, so `hiraku record show` and
    every other tool still read a node as a plain record.
    """

    kind: Kind | None = None
    origin: Origin | None = None
    mode: Mode = "guided"
    expectation: str | None = None
    verdict: Verdict | None = None
    approved_by: str | None = None
    approved_at: str | None = None
    abandoned: bool = False
    triaged: bool = False
    analyzed_runs: list[str] | None = None
    execution_defect: bool = False
    defect_runs: list[str] | None = None

    @property
    def is_proposal(self) -> bool:
        return self.commit is None


def is_approved(node: Node) -> bool:
    """Whether a node may be implemented and launched."""
    return node.mode == "auto" or node.approved_by is not None


def node_state(
    node: Node, runs: list[Run], *, has_children: bool, running: bool
) -> str:
    """A node's place on its lifecycle, derived from its evidence."""
    if node.abandoned:
        return "completed"
    if node.is_proposal:
        return "proposed"
    if running:
        return "running"
    if node.verdict is not None:
        return "completed" if has_children or node.triaged else "concluded"
    return "awaiting_analysis" if runs else "implemented"


def run_status(run: Run) -> str | None:
    for key in ("status", "state"):
        value = run.raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return None


def run_in_flight(run: Run) -> bool:
    """Whether a run reports it is still running, unless it is too old to believe."""
    if run_status(run) not in RUNNING_STATUSES:
        return False
    started = run.timestamp
    return started is None or datetime.now(UTC) - started <= MAX_RUN_AGE


@dataclass
class Tree:
    """A task, its nodes, and the facts the scheduler derives from them."""

    task: Record
    nodes: list[Node]
    runs: dict[str, list[Run]] = field(default_factory=dict)
    states: dict[str, str] = field(default_factory=dict)
    children: dict[str, list[str]] = field(default_factory=dict)
    in_flight: dict[str, str] = field(default_factory=dict)

    @property
    def by_id(self) -> dict[str, Node]:
        return {node.id: node for node in self.nodes}


def load_tree(task_id: str, config: HirakuConfig) -> Tree:
    """Read the task's subtree and its runs from one pass over the tracking source."""
    task = load_record(task_id, config=config)
    nodes = [
        Node.load(record.path)
        for record in task.descendants(config=config)
        if record.path is not None
    ]
    tree = Tree(task=task, nodes=nodes)
    backend = task.resolve_tracking_backend(config=config)
    attributed: dict[str, list[Run]] = defaultdict(list)
    for run in backend.list_runs(record_id=None):
        if run.record_id is not None:
            attributed[run.record_id].append(run)
    children: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        if node.parent_id:
            children[node.parent_id].append(node.id)
    for node in nodes:
        runs = attributed.get(node.id, [])
        tree.runs[node.id] = runs
        tree.children[node.id] = children.get(node.id, [])
        live = next((run for run in runs if run_in_flight(run)), None)
        if live is not None:
            tree.in_flight[node.id] = f"run {live.id} reports {run_status(live)}"
        tree.states[node.id] = node_state(
            node,
            runs,
            has_children=bool(tree.children[node.id]),
            running=node.id in tree.in_flight,
        )
    return tree


# ------------------------------------------------------------------------ scheduler


def suggest(tree: Tree) -> dict[str, Any]:
    """The single next action for the tree, by the fixed priority order."""
    if not tree.nodes:
        return {"action": "grow", "why": "the task has no experiments yet"}
    nodes = tree.by_id
    states = tree.states
    for node_id, state in states.items():
        if state == "proposed" and is_approved(nodes[node_id]):
            return _step("implement", node_id, "approved, but no code bound to it yet")
    for node_id, state in states.items():
        if state == "implemented" and is_approved(nodes[node_id]):
            return _step("launch", node_id, "implemented and approved, never launched")
    for node_id, state in states.items():
        if state == "awaiting_analysis" and _needs_person(tree, node_id) is None:
            return _step("analyze", node_id, "has results, no verdict yet")
    for node_id, state in states.items():
        if state == "concluded":
            verdict = nodes[node_id].verdict
            return _step(
                "triage", node_id, f"verdict is {verdict!r}; decide whether to go on"
            )
    for node_id, state in states.items():
        reason = _needs_person(tree, node_id) if state == "awaiting_analysis" else None
        if reason == "fix":
            return _step(
                "fix",
                node_id,
                "triage found an execution defect: the runs did not test the "
                "expectation",
            )
        if reason == "needs_evidence":
            return _step(
                "needs_evidence",
                node_id,
                "the last analysis reached no verdict and no new runs arrived since",
            )
    for node_id, state in states.items():
        if state == "implemented" and not is_approved(nodes[node_id]):
            return _step(
                "approve",
                node_id,
                "implemented but not approved: check the code still matches the "
                "expectation, then approve",
            )
    for node_id, state in states.items():
        if state == "proposed":
            return _step("approve", node_id, "awaiting the approval gate")
    for node_id, state in states.items():
        if state == "running":
            return _step(
                "wait", node_id, f"launch in flight ({tree.in_flight[node_id]})"
            )
    return {"action": "grow", "why": "nothing left to schedule"}


def _step(action: str, node: str, why: str) -> dict[str, Any]:
    return {"action": action, "node": node, "why": why}


def _needs_person(tree: Tree, node_id: str) -> str | None:
    node = tree.by_id[node_id]
    run_ids = {run.id for run in tree.runs[node_id]}
    if node.execution_defect and run_ids <= set(node.defect_runs or []):
        return "fix"
    if node.analyzed_runs is not None and run_ids == set(node.analyzed_runs):
        return "needs_evidence"
    return None


# ------------------------------------------------------------------------- sections


def _section_pattern(heading: str) -> re.Pattern[str]:
    return re.compile(
        rf"^## {re.escape(heading.strip())}[ \t]*\r?\n(.*?)(?=^## |\Z)",
        re.MULTILINE | re.DOTALL,
    )


def read_section(markdown: str, heading: str) -> str:
    match = _section_pattern(heading).search(markdown or "")
    return match.group(1).strip() if match else ""


def set_section(markdown: str, heading: str, body: str) -> str:
    """Replace a `## heading` section, or append it at the end."""
    block = f"## {heading}\n\n{body.strip()}\n\n"
    pattern = _section_pattern(heading)
    base = markdown or ""
    if pattern.search(base):
        return pattern.sub(lambda _match: block, base, count=1)
    prefix = base if base.endswith("\n") or not base.strip() else base + "\n"
    separator = "\n" if prefix.strip() else ""
    return f"{prefix}{separator}{block}"


def append_section(markdown: str, heading: str, body: str) -> str:
    existing = read_section(markdown, heading)
    return set_section(
        markdown, heading, f"{existing}\n\n{body.strip()}" if existing else body
    )


# ------------------------------------------------------------- metric comparisons


def _metric_values(runs: list[Run], name: str) -> list[float]:
    values = []
    for run in runs:
        raw = run.metrics.get(name)
        if raw is None or isinstance(raw, bool):
            continue
        try:
            number = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            values.append(number)
    return values


def _stat(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "std": None, "values": []}
    mean = sum(values) / len(values)
    std = (
        math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))
        if len(values) > 1
        else None
    )
    return {"n": len(values), "mean": mean, "std": std, "values": values}


def compare_to_baselines(tree: Tree, focus: str) -> dict[str, list[dict[str, Any]]]:
    """The focus node's metrics against every baseline node with runs.

    Computed in code so the agent interprets numbers instead of transcribing them.
    `improvement` is signed so that positive is better in the declared direction.
    """
    metrics = [(item.name, item.direction) for item in tree.task.metrics or []]
    defective = set(tree.by_id[focus].defect_runs or [])
    focus_runs = [run for run in tree.runs[focus] if run.id not in defective]
    result: dict[str, list[dict[str, Any]]] = {}
    for node in tree.nodes:
        if node.id == focus or node.kind != "baseline" or not tree.runs[node.id]:
            continue
        rows = []
        for name, direction in metrics:
            base = _stat(_metric_values(tree.runs[node.id], name))
            cand = _stat(_metric_values(focus_runs, name))
            delta = gain = scaled = None
            if base["mean"] is not None and cand["mean"] is not None:
                delta = cand["mean"] - base["mean"]
                gain = delta if direction == "maximize" else -delta
                scaled = gain / base["std"] if base["std"] else None
            rows.append(
                {
                    "metric": name,
                    "direction": direction,
                    "baseline": base,
                    "candidate": cand,
                    "delta": delta,
                    "improvement": gain,
                    "improvement_in_baseline_std": scaled,
                }
            )
        result[node.id] = rows
    return result


def comparison_markdown(comparison: dict[str, list[dict[str, Any]]]) -> str:
    def number(value: float | None) -> str:
        return "—" if value is None else f"{value:.4g}"

    def cell(stat: dict[str, Any]) -> str:
        if stat["mean"] is None:
            return "—"
        spread = f" ± {stat['std']:.2g}" if stat["std"] is not None else ""
        return f"{number(stat['mean'])}{spread} (n={stat['n']})"

    blocks = [
        "_Computed from the run table by the tool, not by the agent. `improvement` is "
        "the difference signed so that positive is better in the declared direction; "
        "`/ baseline std` needs at least two baseline runs._"
    ]
    for baseline, rows in comparison.items():
        lines = [
            f"**vs `{baseline}`**",
            "",
            "| metric | direction | baseline | this node | delta | improvement "
            "| / baseline std |",
            "|---|---|---|---|---|---|---|",
        ]
        lines += [
            f"| `{row['metric']}` | {row['direction']} | {cell(row['baseline'])} "
            f"| {cell(row['candidate'])} | {number(row['delta'])} "
            f"| {number(row['improvement'])} "
            f"| {number(row['improvement_in_baseline_std'])} |"
            for row in rows
        ]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# --------------------------------------------------------------------- tree state


def tree_state(tree: Tree, focus: str | None = None) -> dict[str, Any]:
    """The task and its nodes as the agent sees them, bounded in size.

    Once the tree is too large, completed nodes off the focus node's line keep only
    their facts and the head of their conclusions; `compacted` lists them, so the
    agent knows to open the record file for the rest.
    """
    nodes: dict[str, Any] = {}
    for node in tree.nodes:
        payload = json_value(node)
        payload.update(
            runs=json_value(tree.runs[node.id]),
            is_proposal=node.is_proposal,
            approved=is_approved(node),
            state=tree.states[node.id],
            children=tree.children[node.id],
        )
        nodes[node.id] = payload
    state: dict[str, Any] = {
        "task": json_value(tree.task),
        "nodes": nodes,
        "focus_node": focus,
    }
    if len(json.dumps(state, default=str)) > STATE_BUDGET_CHARS:
        keep = _focus_line(tree, focus)
        compacted = []
        for node in tree.nodes:
            if node.id in keep or tree.states[node.id] != "completed":
                continue
            excerpt = "\n\n".join(
                f"## {heading}\n\n{read_section(node.markdown, heading)[:limit]}"
                for heading, limit in (
                    ("Title", 200),
                    ("Why declined", 600),
                    ("Conclusions", 1200),
                )
                if read_section(node.markdown, heading)
            )
            nodes[node.id]["markdown"] = excerpt
            compacted.append(node.id)
        state["compacted"] = compacted
    return state


def _focus_line(tree: Tree, focus: str | None) -> set[str]:
    if focus is None or focus not in tree.by_id:
        return set()
    line = {focus, *tree.children[focus]}
    parent = tree.by_id[focus].parent_id
    while parent is not None and parent in tree.by_id:
        line.add(parent)
        parent = tree.by_id[parent].parent_id
    return line


# ------------------------------------------------------------------ entrypoints

_PLACEHOLDERS = (
    (re.compile(r"<[^<>\s]+>"), "a `<...>` placeholder"),
    (re.compile(r"\{\{[^{}]*\}\}|\{[A-Za-z_][\w.-]*\}"), "a `{...}` placeholder"),
    (re.compile(r"\$\{?[A-Za-z_(]"), "a `$` expansion"),
    (re.compile(r"`"), "a backtick expansion"),
    (re.compile(r"UNSET"), "an UNSET marker"),
    (re.compile(r"^~(/|$)"), "a `~` path"),
)
_SCRIPT_SUFFIXES = (".sh", ".sbatch", ".bash", ".py")


def entrypoint_problems(
    entrypoints: dict[str, Any], *, root: str, commit: str
) -> list[str]:
    """Certain failures of entrypoints as `hiraku launch` would run them."""
    problems = []
    for name, raw in entrypoints.items():
        try:
            entry = Entrypoint.model_validate(raw)
            flags = render_arguments(entry.args)
        except (HirakuError, ValueError) as exc:
            problems.append(f"entrypoint {name!r} is not valid: {exc}")
            continue
        for token in [*entry.command, *flags]:
            if token == "":
                problems.append(f"entrypoint {name!r} passes an empty argument")
                continue
            for pattern, what in _PLACEHOLDERS:
                if pattern.search(token):
                    problems.append(
                        f"entrypoint {name!r}: `{token}` contains {what}; hiraku "
                        "passes it unchanged"
                    )
                    break
        for token in entry.command:
            if token.startswith(("-", "/")) or "://" in token or "=" in token:
                continue
            if not token.endswith(_SCRIPT_SUFFIXES):
                continue
            path = posixpath.normpath(token)
            if path.startswith(".."):
                continue
            found = subprocess.run(
                ["git", "-C", root, "cat-file", "-e", f"{commit}:{path}"],
                capture_output=True,
                check=False,
            )
            if found.returncode != 0:
                problems.append(
                    f"entrypoint {name!r} runs `{path}`, which is not in the commit"
                )
    return problems


# ----------------------------------------------------------------- reply shapes


class _Item(BaseModel):
    model_config = ConfigDict(extra="allow")


class OpenItem(_Item):
    item: str
    value: str | None = None
    status: Literal["found", "default", "choice", "unset"] = "unset"
    source: str | None = None


class DesignItem(_Item):
    id: str
    kind: Kind = "hypothesis"
    origin: Origin = "agent-diagnostic"
    parent: str | None = None
    expectation: str
    scope: str | None = None
    expectation_body: str | None = None
    rationale: str | None = None
    open_items: list[OpenItem] = Field(default_factory=list)


class Proposal(_Item):
    id: str
    kind: Kind = "hypothesis"
    origin: Origin = "agent-literature"
    proposed_parent: str | None = None
    title: str | None = None
    expectation: str
    provenance: str | None = None
    applicability: str | None = None
    prediction: str | None = None
    investigation_test: str | None = None
    cost: str | None = None
    counterargument: str | None = None


class Declined(_Item):
    id: str
    kind: Kind = "hypothesis"
    origin: Origin = "agent-literature"
    title: str | None = None
    expectation: str
    failing_condition: str | None = None
    reason: str | None = None
    belongs_under: str | None = None


class Investigation(_Item):
    findings: str | None = None
    inferences: str | None = None
    open_questions: str | None = None


class Condition(_Item):
    pass_: bool = Field(alias="pass")
    note: str = ""


class Child(_Item):
    id: str
    kind: Literal["diagnostic", "ablation", "hypothesis"] = "diagnostic"
    expectation: str
    scope: str | None = None
    expectation_body: str | None = None


class MetricProposal(_Item):
    name: str
    direction: Literal["minimize", "maximize"]
    role: Literal["primary", "secondary"] | None = None
    rationale: str | None = None


class DefaultItem(_Item):
    item: str
    value: str | None = None
    status: Literal["found", "default", "choice", "unset"] | None = None
    rationale: str | None = None


class CandidateMethods(_Item):
    status: Literal["specified", "unspecified"]
    methods: list[str] = Field(default_factory=list)


_OPEN_ITEM_LABELS = {
    "found": "found in the project",
    "default": "default, to be calibrated",
    "choice": "recommended choice, change if you disagree",
    "unset": "unset, needs the researcher",
}
DEFAULT_DECISION_RULE = (
    "_No rule was given, so this default applies. Revise it before the first "
    "verdict._\n\n"
    "- **Target**: the metric in `## Proposed protocol` closest to the end goal.\n"
    "- **Guards**: the other quality metrics; none may get meaningfully worse.\n"
    "- **Report only**: cost metrics (time, memory).\n"
    "- **Tolerance**: twice the baseline's seed-to-seed standard deviation, from at "
    "least three baseline seeds.\n"
    "- **Verdict against a baseline**: `supports` if the target is better beyond "
    "tolerance and no guard is worse beyond it; `refutes` if the target is worse "
    "beyond tolerance; otherwise `inconclusive`. If it is hard to tell, report a "
    "candidate result and lean `inconclusive`.\n"
    "- **Comparing methods**: drop methods whose guards got worse, rank the rest by "
    "the target, treat differences within tolerance as ties, list cost trade-offs "
    "separately."
)

# ------------------------------------------------------------------------ session


class TreeSession(Session):
    task: str | None = None
    mode: Mode = "guided"
    instruction: str | None = None
    max_steps: int = 25
    max_experiments: int | None = None
    max_runs: int | None = None
    time_budget_minutes: float | None = None
    started_at: str
    steps: int = 0
    experiments: int = 0
    runs: int = 0
    action: str | None = None
    focus: str | None = None
    base_commit: str | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)


Text = Annotated[str | None, Field(description="Markdown, or null.")]


class ResearchTree(Workflow):
    """Grow and settle a task's tree of experiments, one scheduled step at a time.

    The scheduler picks the next step from the tree's evidence: design or discover
    proposals, approve them (guided nodes), implement, launch, analyze runs into a
    verdict, and triage each verdict into follow-up children or a stop. Results are
    written into the node records; every number in a comparison is computed by Hiraku.
    """

    # ----------------------------------------------------------------- start

    @tool
    def start(
        self,
        task: Annotated[
            str | None,
            Field(description="Task record ID; defaults to the active record's task."),
        ] = None,
        instruction: Annotated[
            str | None,
            Field(
                description="The researcher's request. Without a task it drafts one."
            ),
        ] = None,
        mode: Annotated[
            Mode,
            Field(description="Mode of new nodes: guided needs approvals, auto not."),
        ] = "guided",
        max_steps: Annotated[
            int, Field(gt=0, description="Agent steps before the session stops.")
        ] = 25,
        max_experiments: Annotated[
            int | None, Field(gt=0, description="Nodes to implement at most.")
        ] = None,
        max_runs: Annotated[
            int | None, Field(gt=0, description="Runs to submit at most.")
        ] = None,
        time_budget_minutes: Annotated[
            float | None, Field(gt=0, description="Wall-clock budget.")
        ] = None,
    ) -> Instruction | Completion:
        """Start working on a task's experiment tree, or draft a new task."""
        session = TreeSession(
            mode=mode,
            instruction=instruction,
            max_steps=max_steps,
            max_experiments=max_experiments,
            max_runs=max_runs,
            time_budget_minutes=time_budget_minutes,
            started_at=datetime.now(UTC).isoformat(),
        )
        session.task = self._task_id(task)
        if session.task is None:
            if not (instruction or "").strip():
                raise SubmissionRejected(
                    "No task: pass a task ID, or an instruction to draft a new task"
                )
            return self._draft(session)
        return self._advance(session)

    # ------------------------------------------------------------ choices

    @tool
    def choose(
        self,
        session: TreeSession,
        step: Annotated[
            Literal["design", "discover"],
            Field(description="design: formalize ideas; discover: search for them."),
        ],
        instruction: Annotated[
            str, Field(description="The researcher's guidance for this step.")
        ] = "",
    ) -> Instruction:
        """Pass on the researcher's choice of how to grow the tree."""
        if session.action != "grow":
            raise SubmissionRejected("No choice is pending")
        if instruction.strip():
            session.instruction = instruction.strip()
        tree = self._tree(session)
        return self._agent_step(session, tree, step, None)

    @tool
    def approve(
        self,
        session: TreeSession,
        approved_by: Annotated[
            str, Field(description="Name of the person who approved the node.")
        ],
    ) -> Instruction | Completion:
        """Record the person's approval of the pending node."""
        node = self._focus_node(session, "approve")
        if not approved_by.strip():
            raise SubmissionRejected("Say who approved the node")
        if node.approved_by is None and node.mode == "guided":
            node.approved_by = approved_by.strip()
            node.approved_at = datetime.now(UTC).isoformat()
            node.save()
        return self._advance(session)

    @tool
    def abandon(
        self,
        session: TreeSession,
        node_id: Annotated[str, Field(description="Node to drop.")],
        reason: Annotated[str, Field(description="Why it is dropped.")],
    ) -> Instruction | Completion:
        """Drop a node on the person's decision; it stays as research history."""
        tree = self._tree(session)
        if node_id not in tree.by_id:
            raise SubmissionRejected(f"{node_id!r} is not a node of this task")
        node = tree.by_id[node_id]
        node.abandoned = True
        node.markdown = set_section(node.markdown, "Why abandoned", reason)
        node.save()
        return self._advance(session)

    @tool
    def check(self, session: TreeSession) -> Instruction | Completion:
        """Look at the tree again, after waiting or after a person acted."""
        if session.action == "wait":
            tree = self._tree(session)
            if session.focus in tree.in_flight:
                raise NotReady(
                    f"{session.focus!r} is still running "
                    f"({tree.in_flight[session.focus]}); check again later"
                )
        return self._advance(session)

    @tool
    def blocked(
        self,
        session: TreeSession,
        reason: Annotated[str, Field(description="What stopped the step.")],
    ) -> Instruction:
        """Report that the current step cannot be done."""
        step = session.action
        session.action = "blocked"
        return self.instruction(
            session,
            self.prompt("#blocked"),
            context={"step": step, "node": session.focus, "reason": reason},
            requires_user_response=True,
            replies=[self.check, self.abandon, self.finish],
        )

    @tool
    def finish(
        self,
        session: TreeSession,
        reason: Annotated[str, Field(description="Why the session ends.")] = "",
    ) -> Completion:
        """End the session and summarize the tree."""
        return self._complete(session, "finished", reason or None)

    # ------------------------------------------------------------- draft

    @tool
    def drafted(
        self,
        session: TreeSession,
        id: Annotated[str, Field(description="Lowercase slug of the new task.")],
        goal: Annotated[str, Field(description="One sentence.")],
        question: Annotated[str, Field(description="One sentence.")],
        summary: Annotated[str, Field(description="One or two sentences.")],
        metrics: list[MetricProposal],
        decision_rule: Text = None,
        body: Text = None,
        proposed_defaults: list[DefaultItem] | None = None,
        candidate_methods: CandidateMethods | None = None,
        notes: Text = None,
    ) -> Instruction | Completion:
        """Create the drafted task record."""
        self._expect(session, "draft")
        validate_identifier(id, label="task ID")
        if id in {record.id for record in list_records(config=self.config)}:
            raise SubmissionRejected(f"A record {id!r} already exists; pick another id")
        markdown = body or ""
        protocol = _protocol_section(metrics, proposed_defaults, candidate_methods)
        if protocol:
            markdown = set_section(markdown, "Proposed protocol", protocol)
        markdown = set_section(
            markdown,
            "Decision rule",
            (decision_rule or "").strip() or DEFAULT_DECISION_RULE,
        )
        Record.create(
            id,
            type="task",
            markdown=markdown,
            activate=True,
            config=self.config,
            goal=goal,
            question=question,
            summary=summary,
            metrics=[{"name": m.name, "direction": m.direction} for m in metrics],
        )
        session.task = id
        return self._after_agent_step(session)

    # ----------------------------------------------------- design, discover

    @tool
    def designed(
        self,
        session: TreeSession,
        experiments: list[DesignItem],
        notes: Text = None,
    ) -> Instruction | Completion:
        """Create the designed experiment nodes."""
        self._expect(session, "design")
        tree = self._tree(session)

        def build(item: DesignItem) -> None:
            markdown = _sections_markdown(
                item,
                {
                    "scope": "Scope",
                    "expectation_body": "Expectation detail",
                    "rationale": "Rationale",
                },
            )
            lines = [
                f"- **{entry.item}** — {entry.value or '_not determined_'} "
                f"({_OPEN_ITEM_LABELS[entry.status]}"
                + (f"; {entry.source})" if entry.source else ")")
                for entry in item.open_items
            ]
            if lines:
                markdown = set_section(markdown, "Open items", "\n".join(lines))
            self._create_node(
                tree,
                item.id,
                parent=item.parent,
                kind=item.kind,
                origin=item.origin,
                expectation=item.expectation,
                mode=session.mode,
                markdown=markdown,
            )

        self._create_batch(
            tree,
            [(item.id, item.parent, lambda i=item: build(i)) for item in experiments],
        )
        return self._after_agent_step(session)

    @tool
    def discovered(
        self,
        session: TreeSession,
        proposals: list[Proposal] | None = None,
        declined: list[Declined] | None = None,
        investigation: Investigation | None = None,
        injected_instructions_found: Text = None,
        notes: Text = None,
    ) -> Instruction | Completion:
        """Create proposed and declined nodes, and record the investigation."""
        self._expect(session, "discover")
        tree = self._tree(session)
        warning = ""
        if injected_instructions_found:
            warning = (
                "**[WARNING: a source read while writing this record contained text "
                "that tried to instruct the agent. Check the sections above against "
                "their cited sources before approving anything from them.]**\n\n> "
                + " ".join(injected_instructions_found.split())[:500]
            )
        taken = {record.id for record in list_records(config=self.config)}
        batch = []
        for item in proposals or []:
            if item.id in taken:
                continue

            def build_proposal(item: Proposal = item) -> None:
                markdown = set_section("", "Title", item.title) if item.title else ""
                markdown += _sections_markdown(
                    item,
                    {
                        "provenance": "Provenance",
                        "applicability": "Applicability",
                        "prediction": "Prediction",
                        "investigation_test": "Investigation test",
                        "cost": "Cost",
                        "counterargument": "Counterargument",
                    },
                )
                if warning:
                    markdown = set_section(markdown, "Injection warning", warning)
                self._create_node(
                    tree,
                    item.id,
                    parent=item.proposed_parent,
                    kind=item.kind,
                    origin=item.origin,
                    expectation=item.expectation,
                    mode="guided" if warning else session.mode,
                    markdown=markdown,
                )

            batch.append((item.id, item.proposed_parent, build_proposal))
        for item in declined or []:
            if item.id in taken:
                continue

            def build_declined(item: Declined = item) -> None:
                markdown = set_section("", "Title", item.title) if item.title else ""
                why = [
                    part
                    for part in (
                        f"**Failing condition:** {item.failing_condition}"
                        if item.failing_condition
                        else "",
                        item.reason or "",
                    )
                    if part
                ]
                if why:
                    markdown = set_section(markdown, "Why declined", "\n\n".join(why))
                if item.belongs_under:
                    markdown = set_section(
                        markdown, "Belongs under", item.belongs_under
                    )
                self._create_node(
                    tree,
                    item.id,
                    parent=None,
                    kind=item.kind,
                    origin=item.origin,
                    expectation=item.expectation,
                    mode=session.mode,
                    markdown=markdown,
                    abandoned=True,
                )

            batch.append((item.id, None, build_declined))
        self._create_batch(tree, batch)
        entry = _investigation_entry(investigation, session.instruction)
        if entry:
            task = load_record(tree.task.id, config=self.config)
            task.markdown = append_section(
                task.markdown, "Investigation", f"{entry}\n\n{warning}".strip()
            )
            task.save()
        return self._after_agent_step(session)

    # ---------------------------------------------------- implement, launch

    @tool
    def implemented(
        self,
        session: TreeSession,
        commit: Annotated[str, Field(description="The commit you made for the node.")],
        entrypoints: Annotated[
            dict[str, Any] | None,
            Field(description="Entrypoints the node needs, or null to keep them."),
        ] = None,
        files_changed: list[str] | None = None,
        notes: Text = None,
    ) -> Instruction | Completion:
        """Bind the commit that implements the node, after checking it."""
        node = self._focus_node(session, "implement")
        if not node.is_proposal:
            raise SubmissionRejected(f"{node.id!r} already has a commit bound")
        if not is_approved(node):
            raise SubmissionRejected(f"{node.id!r} is not approved")
        self._bind(
            session, node, commit, entrypoints, notes, heading="Implementation notes"
        )
        session.experiments += 1
        return self._after_agent_step(session)

    @tool
    def fixed(
        self,
        session: TreeSession,
        commit: Annotated[str, Field(description="The commit with the fix.")],
        entrypoints: Annotated[
            dict[str, Any] | None,
            Field(description="Corrected entrypoints, or null to keep them."),
        ] = None,
        notes: Text = None,
    ) -> Instruction:
        """Bind the commit that fixes the execution defect, then launch again."""
        node = self._focus_node(session, "fix")
        self._bind(session, node, commit, entrypoints, notes, heading="Fix notes")
        return self._launch(session, self._tree(session), node.id)

    @tool
    def launched(
        self,
        session: TreeSession,
        run_ids: Annotated[
            list[str], Field(description="Tracked run IDs the launches produced.")
        ],
    ) -> Instruction | Completion:
        """Submit the tracked runs of the node's launches."""
        if session.action not in {"launch", "needs_evidence"}:
            raise SubmissionRejected("No launch is pending")
        if not run_ids:
            raise SubmissionRejected("Submit at least one run ID")
        tree = self._tree(session)
        node = tree.by_id[str(session.focus)]
        known = {run.id for run in tree.runs[node.id]}
        missing = [run_id for run_id in run_ids if run_id not in known]
        if missing:
            raise NotReady(
                f"Runs {', '.join(missing)} are not attributed to {node.id!r} in the "
                "tracking backend yet; check again once they are recorded"
            )
        old = set(node.defect_runs or []) | set(node.analyzed_runs or [])
        if session.action == "needs_evidence" and not set(run_ids) - old:
            raise SubmissionRejected(
                "These runs were already analysed; launch new runs first"
            )
        session.runs += len(run_ids)
        return self._after_agent_step(session)

    # ------------------------------------------------------ analyze, triage

    @tool
    def analyzed(
        self,
        session: TreeSession,
        verdict: Verdict | None,
        findings: Text = None,
        conclusions: Text = None,
        next_steps: Text = None,
        promotable_candidate: Annotated[
            str | None, Field(description="A run worth a person's look, or null.")
        ] = None,
        missing_evidence: Text = None,
    ) -> Instruction | Completion:
        """Write the analysis of the node's runs."""
        node = self._focus_node(session, "analyze")
        if verdict is None and not (missing_evidence or "").strip():
            raise SubmissionRejected(
                "Without a verdict, say what is missing in missing_evidence"
            )
        tree = self._tree(session)
        comparison = compare_to_baselines(tree, node.id)
        markdown = node.markdown
        if comparison:
            markdown = set_section(
                markdown, "Metric comparison", comparison_markdown(comparison)
            )
        for value, heading in (
            (findings, "Findings"),
            (conclusions, "Conclusions"),
            (next_steps, "Decisions and next steps"),
        ):
            if value:
                markdown = set_section(markdown, heading, value)
        if missing_evidence:
            markdown = set_section(markdown, "Missing evidence", missing_evidence)
        elif read_section(markdown, "Missing evidence"):
            markdown = set_section(
                markdown, "Missing evidence", "None identified in the latest analysis."
            )
        if verdict is not None:
            node.verdict = verdict
            node.triaged = False
            node.execution_defect = False
        defective = set(node.defect_runs or [])
        node.analyzed_runs = sorted(
            run.id for run in tree.runs[node.id] if run.id not in defective
        )
        if promotable_candidate:
            markdown = append_section(
                markdown,
                "Decisions and next steps",
                f"Candidate run: `{promotable_candidate}` — worth a person looking at "
                "as a possible new reference.",
            )
        node.markdown = markdown
        node.save()
        return self._after_agent_step(session)

    @tool
    def triaged(
        self,
        session: TreeSession,
        failure_class: Literal["A", "B", "C"] | None,
        proceed: bool,
        findings: Text = None,
        conclusions: Text = None,
        class_evidence: Text = None,
        verdict: Literal["refutes", "inconclusive"] | None = None,
        investigation_test: dict[str, Condition] | None = None,
        blocking_condition: str | None = None,
        children: list[Child] | None = None,
    ) -> Instruction | Completion:
        """Record the triage decision and create any follow-up children."""
        node = self._focus_node(session, "triage")
        tree = self._tree(session)
        markdown = node.markdown
        if findings:
            markdown = set_section(markdown, "Findings", findings)
        if conclusions:
            markdown = set_section(markdown, "Conclusions", conclusions)
        if failure_class in {"B", "C"} and verdict is not None:
            node.verdict = verdict
        if failure_class == "A":
            run_ids = sorted(run.id for run in tree.runs[node.id])
            node.verdict = None
            node.triaged = False
            node.execution_defect = True
            node.defect_runs = run_ids
            node.markdown = set_section(
                markdown,
                "Execution defect",
                "\n\n".join(
                    part
                    for part in (
                        (class_evidence or "").strip(),
                        "Runs that did not test the expectation: "
                        + (", ".join(f"`{run}`" for run in run_ids) or "none"),
                        "Fix the code on this node, bind the new commit and launch "
                        "again; these runs are left out of the next analysis.",
                    )
                    if part
                ),
            )
            node.save()
            return self._after_agent_step(session)
        if proceed and not children:
            raise SubmissionRejected("proceed is true, so propose at least one child")
        if not proceed and not (blocking_condition or "").strip():
            raise SubmissionRejected("Name the blocking condition when not proceeding")
        if investigation_test:
            lines = ["**Investigation test.**", ""]
            lines += [
                f"- `{key}` — {'pass' if test.pass_ else 'FAIL'}. {test.note}".rstrip()
                for key, test in investigation_test.items()
            ]
            if not proceed:
                lines += ["", f"Stopped: condition `{blocking_condition}` failed."]
            markdown = append_section(
                markdown, "Decisions and next steps", "\n".join(lines)
            )
        if proceed:

            def build(child: Child) -> None:
                self._create_node(
                    tree,
                    child.id,
                    parent=node.id,
                    kind=child.kind,
                    origin="agent-diagnostic",
                    expectation=child.expectation,
                    mode=node.mode,
                    markdown=_sections_markdown(
                        child,
                        {"scope": "Scope", "expectation_body": "Expectation detail"},
                    ),
                )

            self._create_batch(
                tree,
                [
                    (child.id, node.id, lambda c=child: build(c))
                    for child in children or []
                ],
            )
        node.triaged = True
        node.markdown = markdown
        node.save()
        return self._after_agent_step(session)

    # ------------------------------------------------------- off-session tools

    @tool
    def next_step(
        self,
        task: Annotated[
            str | None,
            Field(description="Task record ID; defaults to the active record's task."),
        ] = None,
    ) -> dict[str, Any]:
        """Say what the scheduler would do next for a task, without a session."""
        task_id = self._task_id(task)
        if task_id is None:
            raise SubmissionRejected("No task: pass a task record ID")
        tree = load_tree(task_id, self.config)
        return {**suggest(tree), "states": tree.states}

    # ---------------------------------------------------------------- engine

    def _advance(self, session: TreeSession) -> Instruction | Completion:
        """Ask the scheduler for the next step and turn it into an instruction."""
        limit = self._limit_reached(session)
        if limit is not None:
            return self._complete(session, "limit", limit)
        tree = self._tree(session)
        suggestion = suggest(tree)
        action, node_id = suggestion["action"], suggestion.get("node")
        if action == "grow":
            session.action, session.focus = "grow", None
            return self.instruction(
                session,
                self.prompt("#grow"),
                context={
                    "task": tree.task.id,
                    "why": suggestion["why"],
                    "states": tree.states,
                    "instruction": session.instruction,
                },
                requires_user_response=True,
                replies=[self.choose, self.finish],
            )
        assert node_id is not None
        if action in {"implement", "analyze", "triage"}:
            return self._agent_step(session, tree, action, node_id)
        if action == "launch":
            return self._launch(session, tree, node_id)
        session.action, session.focus = action, node_id
        node = tree.by_id[node_id]
        context = {
            "node": json_value(node),
            "state": tree.states[node_id],
            "why": suggestion["why"],
        }
        if action == "approve":
            return self.instruction(
                session,
                self.prompt("#approve"),
                context=context,
                requires_user_response=True,
                replies=[self.approve, self.abandon, self.finish],
            )
        if action == "fix":
            return self.instruction(
                session,
                self.prompt("#fix"),
                context={**context, "defect_runs": node.defect_runs},
                requires_user_response=True,
                replies=[self.fixed, self.abandon, self.finish],
            )
        if action == "needs_evidence":
            return self.instruction(
                session,
                self.prompt("#needs-evidence"),
                context={
                    **context,
                    "missing_evidence": read_section(node.markdown, "Missing evidence"),
                    "commands": _launch_commands(node, self.config),
                },
                requires_user_response=True,
                replies=[self.launched, self.abandon, self.finish],
            )
        return self.instruction(
            session,
            self.prompt("#wait"),
            context=context,
            replies=[self.check, self.finish],
        )

    def _agent_step(
        self, session: TreeSession, tree: Tree, action: str, node_id: str | None
    ) -> Instruction:
        session.action, session.focus = action, node_id
        context: dict[str, Any] = {
            "state": tree_state(tree, node_id),
            "instruction": session.instruction,
        }
        replies: dict[str, Any] = {
            "design": self.designed,
            "discover": self.discovered,
            "implement": self.implemented,
            "analyze": self.analyzed,
            "triage": self.triaged,
        }
        if action == "implement" and node_id is not None:
            node = tree.by_id[node_id]
            parent = tree.by_id.get(node.parent_id or "")
            base = parent.commit if parent is not None else None
            if base is None:
                try:
                    base = GitRepository(self.config).head
                except GitError:
                    base = None
            session.base_commit = base
            context.update(
                branch=self.config.branch.format(record_id=node_id),
                base_commit=base,
                tracking_backend=self.config.tracking_backend,
            )
        if action == "analyze" and node_id is not None:
            comparison = compare_to_baselines(tree, node_id)
            if comparison:
                context["metric_comparison"] = comparison
        return self.instruction(
            session,
            self.prompt(f"#{action}"),
            context=context,
            replies=[replies[action], self.blocked, self.finish],
        )

    def _draft(self, session: TreeSession) -> Instruction:
        session.action, session.focus = "draft", None
        tasks = [
            {"id": record.id, "goal": getattr(record, "goal", None)}
            for record in list_records(config=self.config)
            if record.parent_id is None
        ]
        return self.instruction(
            session,
            self.prompt("#draft"),
            context={"instruction": session.instruction, "existing_tasks": tasks},
            replies=[self.drafted, self.blocked, self.finish],
        )

    def _launch(self, session: TreeSession, tree: Tree, node_id: str) -> Instruction:
        session.action, session.focus = "launch", node_id
        node = tree.by_id[node_id]
        return self.instruction(
            session,
            self.prompt("#launch"),
            context={
                "node": node_id,
                "commit": node.commit,
                "branch": node.branch or self.config.branch.format(record_id=node_id),
                "commands": _launch_commands(node, self.config),
            },
            replies=[self.launched, self.blocked, self.finish],
        )

    def _after_agent_step(self, session: TreeSession) -> Instruction | Completion:
        session.history.append({"step": session.action, "node": session.focus})
        session.steps += 1
        return self._advance(session)

    def _limit_reached(self, session: TreeSession) -> str | None:
        if session.steps >= session.max_steps:
            return f"max_steps ({session.max_steps}) reached"
        if session.max_experiments and session.experiments >= session.max_experiments:
            return f"max_experiments ({session.max_experiments}) reached"
        if session.max_runs and session.runs >= session.max_runs:
            return f"max_runs ({session.max_runs}) reached"
        if session.time_budget_minutes:
            elapsed = datetime.now(UTC) - datetime.fromisoformat(session.started_at)
            if elapsed >= timedelta(minutes=session.time_budget_minutes):
                return f"time budget ({session.time_budget_minutes} min) used"
        return None

    def _complete(
        self, session: TreeSession, outcome: str, reason: str | None
    ) -> Completion:
        states: dict[str, str] = {}
        if session.task is not None:
            tree = self._tree(session)
            states = tree.states
        return self.completion(
            session,
            {
                "outcome": outcome,
                "reason": reason,
                "task": session.task,
                "steps": session.history,
                "experiments_implemented": session.experiments,
                "runs_submitted": session.runs,
                "states": states,
            },
        )

    # --------------------------------------------------------------- helpers

    def _task_id(self, task: str | None) -> str | None:
        if task is not None:
            record = load_record(task, config=self.config)
            if record.parent_id is not None:
                raise SubmissionRejected(f"{task!r} is not a task (a root record)")
            return record.id
        active = self.config.active_record_id
        if active is None:
            return None
        record = load_record(active, config=self.config)
        while record.parent_id is not None:
            record = load_record(record.parent_id, config=self.config)
        return record.id

    def _tree(self, session: TreeSession) -> Tree:
        if session.task is None:
            raise SubmissionRejected("The session has no task yet")
        return load_tree(session.task, self.config)

    def _expect(self, session: TreeSession, action: str) -> None:
        if session.action != action:
            raise SubmissionRejected(f"No {action} step is pending")

    def _focus_node(self, session: TreeSession, action: str) -> Node:
        self._expect(session, action)
        tree = self._tree(session)
        if session.focus not in tree.by_id:
            raise SubmissionRejected(f"Node {session.focus!r} no longer exists")
        return tree.by_id[str(session.focus)]

    def _create_node(
        self,
        tree: Tree,
        node_id: str,
        *,
        parent: str | None,
        markdown: str,
        **fields: Any,
    ) -> None:
        Record.create(
            node_id,
            parent_id=parent or tree.task.id,
            markdown=markdown,
            activate=False,
            config=self.config,
            **{key: value for key, value in fields.items() if value is not None},
        )

    def _create_batch(
        self, tree: Tree, batch: list[tuple[str, str | None, Any]]
    ) -> None:
        """Create nodes all-or-nothing, parents first, within the task's tree."""
        ids = [identifier for identifier, _, _ in batch]
        for identifier in ids:
            validate_identifier(identifier, label="node ID")
        if len(ids) != len(set(ids)):
            raise SubmissionRejected("The reply repeats a node ID")
        taken = {record.id for record in list_records(config=self.config)}
        clashes = sorted(set(ids) & taken)
        if clashes:
            raise SubmissionRejected(
                f"IDs {', '.join(clashes)} already exist; IDs are unique project-wide"
            )
        allowed = {tree.task.id, *tree.by_id, *ids}
        stray = [f"{i} -> {p}" for i, p, _ in batch if p and p not in allowed]
        if stray:
            raise SubmissionRejected(
                f"Parents outside task {tree.task.id!r}: {', '.join(stray)}"
            )
        pending = {identifier: (parent, build) for identifier, parent, build in batch}
        created: list[str] = []
        try:
            while pending:
                ready = [
                    identifier
                    for identifier, (parent, _) in pending.items()
                    if parent not in pending or parent == identifier
                ]
                if not ready:
                    raise SubmissionRejected("The reply's parents form a cycle")
                for identifier in ready:
                    if pending[identifier][0] == identifier:
                        raise SubmissionRejected(f"{identifier!r} is its own parent")
                    pending.pop(identifier)[1]()
                    created.append(identifier)
        except Exception:
            for identifier in reversed(created):
                load_record(identifier, config=self.config).delete(config=self.config)
            raise

    def _bind(
        self,
        session: TreeSession,
        node: Node,
        commit: str,
        entrypoints: dict[str, Any] | None,
        notes: str | None,
        *,
        heading: str,
    ) -> None:
        repository = GitRepository(self.config)
        resolved = repository.resolve(commit)
        base = session.base_commit or node.commit
        if base is not None:
            if resolved == base or not repository.is_ancestor(base, resolved):
                raise SubmissionRejected(
                    f"Commit {resolved[:12]} is not a new commit on top of "
                    f"{base[:12]}; commit the change and reply with the new head"
                )
        elif any(other.commit == resolved for other in self._tree(session).nodes):
            raise SubmissionRejected(f"Commit {resolved[:12]} is bound to another node")
        merged = {
            name: json_value(entry) for name, entry in (node.entrypoints or {}).items()
        }
        merged.update(entrypoints or {})
        effective = node.resolve(config=self.config).values.get("entrypoints") or {}
        checked = {**{k: json_value(v) for k, v in effective.items()}, **merged}
        problems = entrypoint_problems(
            checked, root=str(repository.root), commit=resolved
        )
        if problems:
            raise SubmissionRejected(
                "The entrypoints would not run as declared, so the commit was not "
                "bound: " + "; ".join(problems)
            )
        if merged:
            node.entrypoints = merged
        if notes:
            node.markdown = set_section(node.markdown, heading, notes)
        node.commit = resolved
        node.save()
        session.base_commit = None


def _launch_commands(node: Node, config: HirakuConfig) -> list[str]:
    entrypoints = node.resolve(config=config).values.get("entrypoints") or {}
    return [
        f"hiraku launch {name} --record {node.id} --commit {node.commit}"
        for name in sorted(entrypoints)
    ]


def _sections_markdown(item: BaseModel, mapping: dict[str, str]) -> str:
    markdown = ""
    for key, heading in mapping.items():
        value = getattr(item, key, None)
        if value:
            markdown = set_section(markdown, heading, str(value))
    return markdown


def _protocol_section(
    metrics: list[MetricProposal],
    defaults: list[DefaultItem] | None,
    candidates: CandidateMethods | None,
) -> str:
    lines = [
        "_Chosen by the agent where the instruction left the protocol open. "
        "Edit freely._",
        "",
    ]
    metric_lines = []
    for metric in metrics:
        detail = (
            f"{metric.direction}, {metric.role}" if metric.role else metric.direction
        )
        line = f"- `{metric.name}` ({detail})"
        metric_lines.append(
            f"{line} — {metric.rationale}" if metric.rationale else line
        )
    if metric_lines:
        lines += ["**Metrics**", "", *metric_lines, ""]
    default_lines = []
    for item in defaults or []:
        line = f"- **{item.item}**: {item.value or ''}"
        why = item.rationale or ""
        if item.status:
            label = _OPEN_ITEM_LABELS[item.status]
            why = f"{label}; {why}" if why else label
        default_lines.append(f"{line} — {why}" if why else line)
    if default_lines:
        lines += ["**Defaults**", "", *default_lines, ""]
    if candidates is not None:
        if candidates.status == "specified" and candidates.methods:
            lines += [
                "**Candidate methods**",
                "",
                f"- Status: specified by the researcher ({len(candidates.methods)} "
                "methods).",
                *[f"- {method}" for method in candidates.methods],
                "",
            ]
        elif candidates.status == "unspecified":
            lines += [
                "**Candidate methods**",
                "",
                "- Status: unspecified — the instruction compares methods without "
                "naming them. Discover candidates before designing the comparison.",
                "",
            ]
    if not metric_lines and not default_lines and candidates is None:
        return ""
    return "\n".join(lines).rstrip()


def _investigation_entry(
    investigation: Investigation | None, instruction: str | None
) -> str:
    if investigation is None:
        return ""
    parts = [
        f"{label}\n\n{text.strip()}"
        for text, label in (
            (
                investigation.findings,
                "**Established** (from files, logs, code or sources — each cited)",
            ),
            (investigation.inferences, "**Inferred, not verified**"),
            (investigation.open_questions, "**Open questions**"),
        )
        if text and text.strip()
    ]
    if not parts:
        return ""
    focus = " ".join((instruction or "").split())[:80] or "investigation"
    heading = f"### {datetime.now(UTC).date().isoformat()} — {focus}"
    return "\n\n".join([heading, *parts])


WORKFLOW = ResearchTree
