"""Research: run a task end to end over an experiment tree.

The workflow adopts or drafts a task, designs experiments under it, and takes
each experiment through implement, launch, analyze, and triage. Triage can open
follow-up experiments as children of the experiment they respond to, so the
experiments form a tree. After every accepted event, Python picks the next step:
launches first, then implementations, triage, analyses, and designs, each in
breadth-first order. An experiment's step is derived from the evidence on its
record (commit, attributed runs, verdict, triage decision), not stored, so
edits made outside the session are picked up.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from hiraku.context import load_context
from hiraku.errors import EventRejected, HirakuError, NotReady
from hiraku.git import GitRepository
from hiraku.record import Record, list_records, load_record
from hiraku.run import Run
from hiraku.workflows.graph import State, event, node, start
from hiraku.workflows.workflow import Instruction, Workflow

Verdict = Literal["supports", "refutes", "inconclusive"]
Decision = Literal["follow-up", "stop"]
# Steps that need the agent, in the order they are picked. Work that starts
# compute comes first, so jobs run while the agent analyzes and designs; triage
# precedes analysis, so an analyzed experiment is triaged right away.
# "waiting" experiments only need time.
STEPS = ("launch", "implement", "triage", "analyze", "design")
WORK = [
    "setup",
    "draft",
    "design",
    "implement",
    "launch",
    "wait",
    "analyze",
    "triage",
]
NODES = [*WORK, "finished"]
APPROVAL = (
    "Present this to the user and wait for their approval before you send the "
    "event; apply their changes first."
)
AUTONOMOUS = "Decide on your own; do not wait for the user."
# The context that says how experiments run in this project; setup creates it
# and every later instruction refers to it.
SETUP_CONTEXT = "project-setup"
READ_CONTEXTS = "Read the Hiraku contexts referenced below before you start."


class Plan(BaseModel):
    """One experiment proposed by the design step."""

    record_id: str = Field(description="Short ID of the new experiment record.")
    summary: str = Field(description="The experiment in one line.")
    hypothesis: str = Field(description="What the experiment tests.")
    expected: str = Field(
        description="The outcome that would support the hypothesis, in metric terms."
    )
    change: str = Field(description="The planned code change.")


class Study(State):
    node: str = "draft"
    task: str | None = None
    entrypoint: str = "train"
    max_experiments: int = 6
    max_depth: int = 3
    max_repairs: int = 2
    # The experiment the agent works on, or the parent to design under.
    current: str | None = None


class Research(Workflow):
    """Run a research task end to end over an experiment tree.

    Experiments are records under the task; a follow-up experiment is a child of
    the experiment whose result it responds to. The agent carries out one step
    at a time; Hiraku checks each report against the records, writes them, and
    picks the next step from the tree.
    """

    # Settings, overridable in the front matter of workflow.md.
    rules: str = ""
    autonomous: bool = False

    @start(dest=NODES)
    def start(
        self,
        *,
        task: Annotated[
            str | None,
            "Task record; defaults to the active record if it is a task, "
            "otherwise the workflow drafts one with you.",
        ] = None,
        entrypoint: Annotated[str, "Entrypoint that runs one experiment."] = "train",
        max_experiments: Annotated[
            int, "Most experiments in the tree.", Field(gt=0, le=50)
        ] = 6,
        max_depth: Annotated[
            int,
            "Deepest level of experiments; 1 allows no follow-ups.",
            Field(gt=0, le=10),
        ] = 3,
        max_repairs: Annotated[
            int, "Repairs after failed runs before giving up.", Field(ge=0, le=5)
        ] = 2,
    ) -> Study:
        """Start, or continue, a study on a task record."""
        with _checked():
            study = Study(
                entrypoint=entrypoint,
                max_experiments=max_experiments,
                max_depth=max_depth,
                max_repairs=max_repairs,
            )
            if task is None:
                active = self.config.active_record_id
                if (
                    active is not None
                    and load_record(active, config=self.config).type == "task"
                ):
                    task = active
            if task is not None:
                self._check_task(task, entrypoint)
                study.task = task
            return self._assign(study)

    # Nodes: one instruction per step ---------------------------------------------

    @node("setup")
    def setting_up(self, study: Study) -> Instruction:
        config = self.config
        data = {
            "problems": self._setup_problems(),
            "slurm": shutil.which("sbatch") is not None,
            "default_dispatch": config.default_dispatch,
            "dispatchers": sorted(
                path.name for path in config.dispatch_dir.glob("*") if path.is_file()
            ),
            "job_scripts": _job_scripts(config.root),
            "tracking_backend": config.tracking_backend,
            "context": SETUP_CONTEXT,
        }
        return self._step("## Setup", data, contexts=False)

    @node("draft")
    def drafting(self, study: Study) -> Instruction:
        return self._step("## Draft", {"entrypoint": study.entrypoint}, gate=False)

    @node("design")
    def designing(self, study: Study) -> Instruction:
        parent = self._record(study.current)
        tree = self._tree(study)
        data = {
            "task": study.task,
            "parent": _describe(parent),
            "depth": self._depth(parent, study) + 1,
            "remaining": self._remaining(study, tree),
            "tree": tree,
        }
        return self._step("## Design", data, records=[study.task, parent.id])

    @node("implement")
    def implementing(self, study: Study) -> Instruction:
        experiment = self._record(study.current)
        parent = self._record(experiment.parent_id)
        data = {
            "task": study.task,
            "experiment": _describe(experiment),
            "parent_commit": parent.commit if parent.type == "experiment" else None,
            "entrypoint": study.entrypoint,
            "failure": getattr(experiment, "failure", None),
        }
        return self._step(
            "## Implement", data, gate=False, records=[study.task, experiment.id]
        )

    @node("launch")
    def launching(self, study: Study) -> Instruction:
        experiment = self._record(study.current)
        command = shlex.join(
            [
                "hiraku",
                "launch",
                study.entrypoint,
                "--record",
                experiment.id,
                "--commit",
                str(experiment.commit),
            ]
        )
        data = {"experiment": _describe(experiment), "command": command}
        return self._step("## Launch", data, gate=False, records=[experiment.id])

    @node("wait")
    def waiting(self, study: Study) -> Instruction:
        waiting = [
            {"record_id": item["id"], "commit": item["commit"]}
            for item in self._tree(study)
            if item["step"] == "waiting"
        ]
        return self._step("## Wait", {"launched": waiting}, gate=False)

    @node("analyze")
    def analyzing(self, study: Study) -> Instruction:
        experiment = self._record(study.current)
        parent = self._record(experiment.parent_id)
        runs = self._runs(experiment, study)
        task = self._record(study.task)
        data = {
            "experiment": _describe(experiment),
            "parent": _describe(parent) if parent.type == "experiment" else None,
            "metrics": _metrics(task, config=self.config),
            "runs": [
                {
                    "run_id": run.id,
                    "metrics": dict(run.metrics),
                    "params": dict(run.params),
                }
                for run in runs
            ],
        }
        return self._step(
            "## Analyze", data, gate=False, records=[study.task, experiment.id]
        )

    @node("triage")
    def triaging(self, study: Study) -> Instruction:
        experiment = self._record(study.current)
        tree = self._tree(study)
        data = {
            "experiment": _describe(experiment),
            "depth": self._depth(experiment, study),
            "remaining": self._remaining(study, tree),
            "can_follow_up": self._follow_up_problem(experiment, study, tree) is None,
            "tree": tree,
        }
        return self._step("## Triage", data, records=[study.task, experiment.id])

    @node("finished", final=True)
    def summarizing(self, study: Study) -> Instruction:
        return Instruction(
            self.markdown.section("## Done"),
            data={"task": study.task, "tree": self._tree(study) if study.task else []},
        )

    # Events: the agent's reports, checked against the records -------------------

    @event(source="setup", dest=NODES)
    def set_up(self, study: Study) -> Study:
        """Report that the project is set up; Hiraku checks it again."""
        with _checked():
            problems = self._setup_problems()
            if problems:
                raise EventRejected("Still to fix: " + "; ".join(problems))
            return self._assign(study)

    @event(source="draft", dest=NODES)
    def drafted_task(
        self, study: Study, *, record_id: Annotated[str, "ID of the task record."]
    ) -> Study:
        """Submit the task record created with the user."""
        with _checked():
            self._check_task(record_id, study.entrypoint)
            study.task = record_id
            return self._assign(study)

    @event(source="design", dest=NODES)
    def designed(
        self,
        study: Study,
        *,
        experiments: Annotated[
            list[Plan], "The proposed experiments.", Field(min_length=1)
        ],
    ) -> Study:
        """Submit the experiments to create under the parent record."""
        with _checked():
            parent = self._record(study.current)
            tree = self._tree(study)
            remaining = self._remaining(study, tree)
            if len(experiments) > remaining:
                raise EventRejected(
                    f"Propose at most {remaining} experiments; the limit is "
                    f"{study.max_experiments}"
                )
            if parent.type == "experiment" and not getattr(parent, "verdict", None):
                raise EventRejected(f"{parent.id!r} has no verdict yet")
            depth = self._depth(parent, study) + 1
            if depth > study.max_depth:
                raise EventRejected(
                    f"Experiments under {parent.id!r} would be at depth {depth}, "
                    f"deeper than {study.max_depth}"
                )
            existing = {item.id for item in list_records(config=self.config)}
            ids = [plan.record_id for plan in experiments]
            taken = sorted({*ids} & existing)
            if taken or len(ids) != len(set(ids)):
                raise EventRejected(
                    f"Record IDs must be new and distinct; taken: {taken or ids}"
                )
            sequence = max((item["sequence"] for item in tree), default=0)
            for offset, plan in enumerate(experiments, start=1):
                Record.create(
                    plan.record_id,
                    parent_id=parent.id,
                    type="experiment",
                    status="designed",
                    summary=plan.summary,
                    hypothesis=plan.hypothesis,
                    expected=plan.expected,
                    sequence=sequence + offset,
                    markdown=f"## Change\n\n{plan.change.strip()}\n",
                    activate=False,
                    config=self.config,
                )
            return self._assign(study)

    @event(source="implement", dest=NODES)
    def implemented(
        self, study: Study, *, commit: Annotated[str, "Commit with the change."]
    ) -> Study:
        """Submit the commit that implements the experiment."""
        with _checked():
            experiment = self._record(study.current)
            experiment.commit = GitRepository(self.config).resolve(commit)
            experiment.status = "implemented"
            experiment.save()
            return self._assign(study)

    @event(source="launch", dest=NODES)
    def launched(self, study: Study) -> Study:
        """Report that the experiment's launch is submitted."""
        with _checked():
            experiment = self._record(study.current)
            experiment.status = "launched"
            experiment.save()
            return self._assign(study)

    @event(source=["launch", "wait"], dest=NODES)
    def failed(
        self,
        study: Study,
        *,
        record_id: Annotated[str, "ID of the experiment that failed to run."],
        reason: Annotated[str, "The error, from the launch output or job log."],
    ) -> Study:
        """Report that an experiment failed to run; it goes back to implement."""
        with _checked():
            experiment = self._record(record_id)
            step = self._step_of(experiment, study, self._children(study))
            if step not in ("launch", "waiting"):
                raise EventRejected(
                    f"{record_id!r} is not launched or waiting (it needs {step})"
                )
            repairs = int(getattr(experiment, "repairs", 0) or 0) + 1
            experiment.repairs = repairs
            experiment.failure = reason
            if repairs > study.max_repairs:
                experiment.status = "analyzed"
                experiment.verdict = "inconclusive"
                experiment.conclusion = (
                    f"Could not be run after {study.max_repairs} repairs: {reason}"
                )
            else:
                experiment.commit = None
                experiment.status = "designed"
            experiment.save()
            return self._assign(study)

    @event(source="wait", dest=NODES)
    def checked(self, study: Study) -> Study:
        """Ask Hiraku to look for finished runs again."""
        with _checked():
            self._assign(study)
            if study.node == "wait":
                raise NotReady(
                    "No launched experiment has a run with metrics yet; "
                    "check again later"
                )
            return study

    @event(source="analyze", dest=NODES)
    def analyzed(
        self,
        study: Study,
        *,
        verdict: Annotated[Verdict, "Whether the runs support the hypothesis."],
        conclusion: Annotated[str, "A few sentences citing runs and metrics."],
    ) -> Study:
        """Submit the verdict and conclusion of the experiment."""
        with _checked():
            experiment = self._record(study.current)
            if not any(run.metrics for run in self._runs(experiment, study)):
                raise NotReady(f"The runs of {experiment.id!r} have no metrics yet")
            experiment.verdict = verdict
            experiment.conclusion = conclusion
            experiment.status = "analyzed"
            experiment.save()
            return self._assign(study)

    @event(source="triage", dest=NODES)
    def triaged(
        self,
        study: Study,
        *,
        decision: Annotated[Decision, "Follow up with child experiments, or stop."],
        reason: Annotated[str, "Why, in one sentence."],
    ) -> Study:
        """Submit what follows from the experiment's verdict."""
        with _checked():
            experiment = self._record(study.current)
            if decision == "follow-up":
                problem = self._follow_up_problem(experiment, study, self._tree(study))
                if problem is not None:
                    raise EventRejected(f"{problem}; send stop instead")
            experiment.triage = decision
            experiment.triage_reason = reason
            experiment.status = "triaged"
            experiment.save()
            return self._assign(study)

    @event(source=WORK, dest="finished")
    def finish(self, study: Study) -> Study:
        """End the study early and summarize the tree."""
        study.node = "finished"
        study.current = None
        return study

    # Read-only views ----------------------------------------------------------------

    def overview(self, task: str, entrypoint: str = "train") -> dict[str, Any]:
        """Describe a task's experiment tree for dashboards, without changing it.

        Each experiment carries the step it needs, derived exactly as the
        scheduler derives it, and its runs at its commit.
        """
        study = Study(task=task, entrypoint=entrypoint)
        root = load_record(task, config=self.config)
        children = self._children(study)
        experiments = []
        for experiment, depth in self._ordered(study, children):
            runs = self._runs(experiment, study)
            experiments.append(
                {
                    **_describe(experiment),
                    "parent": experiment.parent_id,
                    "depth": depth,
                    "sequence": _sequence(experiment),
                    "step": self._step_of(experiment, study, children),
                    "status": experiment.status,
                    "triage": getattr(experiment, "triage", None),
                    "triage_reason": getattr(experiment, "triage_reason", None),
                    "failure": getattr(experiment, "failure", None),
                    "repairs": getattr(experiment, "repairs", 0) or 0,
                    "runs": [
                        {"run_id": run.id, "metrics": dict(run.metrics)} for run in runs
                    ],
                }
            )
        return {
            "task": {
                **_describe(root),
                "metrics": _metrics(root, config=self.config),
            },
            "experiments": experiments,
        }

    # Scheduling -------------------------------------------------------------------

    def _assign(self, study: Study) -> Study:
        """Move to the next step.

        Of the open experiments, pick the one whose step comes first in STEPS,
        and among equals the first one breadth first, in creation order.
        """
        study.current = None
        if self._setup_problems():
            study.node = "setup"
            return study
        if study.task is None:
            study.node = "draft"
            return study
        children = self._children(study)
        if not children.get(study.task):
            if self._remaining(study, []) > 0:
                study.node, study.current = "design", study.task
            else:
                study.node = "finished"
            return study
        steps = [
            (self._step_of(experiment, study, children), index, experiment.id)
            for index, (experiment, _depth) in enumerate(self._ordered(study, children))
        ]
        open_steps = [item for item in steps if item[0] in STEPS]
        if open_steps:
            step, _index, record_id = min(
                open_steps, key=lambda item: (STEPS.index(item[0]), item[1])
            )
            study.node, study.current = step, record_id
            return study
        waiting = any(step == "waiting" for step, _index, _id in steps)
        study.node = "wait" if waiting else "finished"
        return study

    def _step_of(
        self, experiment: Record, study: Study, children: dict[str, list[Record]]
    ) -> str:
        """Derive the step an experiment needs from the evidence on its record."""
        if getattr(experiment, "verdict", None):
            triage = getattr(experiment, "triage", None)
            if triage is None:
                return "triage"
            if triage == "follow-up" and not children.get(experiment.id):
                return "design"
            return "done"
        if experiment.commit is None:
            return "implement"
        runs = self._runs(experiment, study)
        if any(run.metrics for run in runs):
            return "analyze"
        if runs or experiment.status == "launched":
            return "waiting"
        return "launch"

    def _children(self, study: Study) -> dict[str, list[Record]]:
        children: dict[str, list[Record]] = {}
        for record in list_records(config=self.config):
            if record.parent_id is not None and record.type == "experiment":
                children.setdefault(record.parent_id, []).append(record)
        for items in children.values():
            items.sort(key=lambda item: (_sequence(item), item.id))
        return children

    def _ordered(
        self, study: Study, children: dict[str, list[Record]]
    ) -> Iterator[tuple[Record, int]]:
        """Experiments under the task, breadth first, in creation order."""
        level, depth = children.get(str(study.task), []), 1
        while level:
            nested = []
            for experiment in level:
                yield experiment, depth
                nested.extend(children.get(experiment.id, []))
            level, depth = nested, depth + 1

    def _tree(self, study: Study) -> list[dict[str, Any]]:
        children = self._children(study)
        return [
            {
                "id": experiment.id,
                "parent": experiment.parent_id,
                "depth": depth,
                "sequence": _sequence(experiment),
                "step": self._step_of(experiment, study, children),
                "summary": getattr(experiment, "summary", None),
                "commit": experiment.commit,
                "verdict": getattr(experiment, "verdict", None),
                "triage": getattr(experiment, "triage", None),
            }
            for experiment, depth in self._ordered(study, children)
        ]

    def _remaining(self, study: Study, tree: list[dict[str, Any]]) -> int:
        return max(study.max_experiments - len(tree), 0)

    def _depth(self, record: Record, study: Study) -> int:
        """Depth below the task: the task is 0, its experiments are 1."""
        depth = 0
        while record.id != study.task:
            if record.parent_id is None:
                raise EventRejected(f"{record.id!r} is not under task {study.task!r}")
            record = self._record(record.parent_id)
            depth += 1
        return depth

    def _follow_up_problem(
        self, experiment: Record, study: Study, tree: list[dict[str, Any]]
    ) -> str | None:
        if self._depth(experiment, study) >= study.max_depth:
            return f"{experiment.id!r} is at the maximum depth {study.max_depth}"
        if self._remaining(study, tree) == 0:
            return f"The study has reached {study.max_experiments} experiments"
        return None

    def _runs(self, experiment: Record, study: Study) -> list[Run]:
        """Runs of the entrypoint at the experiment's commit."""
        return [
            run
            for run in experiment.runs(config=self.config)
            if run.entrypoint == study.entrypoint
            and run.commit in (None, experiment.commit)
        ]

    # Helpers ----------------------------------------------------------------------

    def _check_task(self, task: str, entrypoint: str) -> None:
        record = load_record(task, config=self.config)
        if record.type != "task":
            raise EventRejected(f"{task!r} is a {record.type!r} record, not a task")
        record.entrypoint(entrypoint, config=self.config)
        if not _metrics(record, config=self.config):
            raise EventRejected(
                f"Task {task!r} declares no metrics; add metrics as a list of "
                "{name, direction}"
            )

    def _record(self, record_id: str | None) -> Record:
        if record_id is None:
            raise EventRejected("No record is assigned at this step")
        return load_record(record_id, config=self.config)

    def _step(
        self,
        section: str,
        data: dict[str, Any],
        *,
        gate: bool = True,
        records: list[str | None] | None = None,
        contexts: bool = True,
    ) -> Instruction:
        text = self.markdown.section(section)
        if gate:
            text = f"{text}\n\n{AUTONOMOUS if self.autonomous else APPROVAL}"
        references = list(dict.fromkeys(item for item in records or [] if item))
        context_ids = self._contexts(references) if contexts else []
        if context_ids:
            text = f"{text}\n\n{READ_CONTEXTS}"
        if self.rules:
            text = f"{text}\n\n{self.rules}"
        context: dict[str, list[str]] = {}
        if references:
            context["records"] = references
        if context_ids:
            context["contexts"] = context_ids
        return Instruction(text, data=data, context=context or None)

    def _contexts(self, records: list[str]) -> list[str]:
        """The setup context, then the contexts named on each record's ancestry.

        A record lists context IDs in its `contexts` field; the task's apply to
        the whole tree, an experiment's to it and its follow-ups. Missing
        contexts are left out.
        """
        ids = [SETUP_CONTEXT]
        for record_id in records:
            chain = [load_record(record_id, config=self.config)]
            while chain[-1].parent_id is not None:
                chain.append(load_record(chain[-1].parent_id, config=self.config))
            for record in reversed(chain):
                value = getattr(record, "contexts", None) or []
                ids.extend([value] if isinstance(value, str) else value)
        return [
            context_id
            for context_id in dict.fromkeys(ids)
            if isinstance(context_id, str) and _context_exists(context_id, self)
        ]

    def _setup_problems(self) -> list[str]:
        """What keeps experiments from running in this project, if anything."""
        config = self.config
        problems = []
        if not _context_exists(SETUP_CONTEXT, self):
            problems.append(
                f"no `{SETUP_CONTEXT}` context says how experiments run in this project"
            )
        dispatch = config.default_dispatch
        path = config.dispatch_dir / dispatch
        if not path.is_file():
            problems.append(
                f"the default dispatcher {dispatch} is not in hiraku/dispatch"
            )
        elif not os.access(path, os.X_OK):
            problems.append(f"the default dispatcher {dispatch} is not executable")
        if shutil.which("sbatch") and dispatch in ("launch.sh", "worktree.sh"):
            problems.append(
                f"this is a Slurm cluster, but {dispatch} would run experiments on "
                "this node; set default_dispatch: slurm.sh in hiraku.yml"
            )
        if dispatch == "slurm.sh":
            header = config.dispatch_dir / "slurm.header"
            text = header.read_text(encoding="utf-8") if header.is_file() else ""
            if not any(line.startswith("#SBATCH") for line in text.splitlines()):
                problems.append(
                    "hiraku/dispatch/slurm.header has no #SBATCH lines for this site"
                )
        return problems


@contextmanager
def _checked() -> Iterator[None]:
    """Report Hiraku errors as rejected events, so the agent can correct them."""
    try:
        yield
    except EventRejected:
        raise
    except HirakuError as exc:
        raise EventRejected(str(exc)) from exc


def _describe(record: Record) -> dict[str, Any]:
    return {
        "record_id": record.id,
        "type": record.type,
        "summary": getattr(record, "summary", None),
        "hypothesis": getattr(record, "hypothesis", None),
        "expected": getattr(record, "expected", None),
        "change": record.markdown.strip() or None,
        "commit": record.commit,
        "verdict": getattr(record, "verdict", None),
        "conclusion": getattr(record, "conclusion", None),
    }


def _metrics(record: Record, *, config: Any = None) -> list[dict[str, Any]]:
    values = record.resolve(config=config).values.get("metrics") or []
    return [
        item.model_dump() if isinstance(item, BaseModel) else dict(item)
        for item in values
    ]


def _context_exists(context_id: str, workflow: Workflow) -> bool:
    try:
        load_context(context_id, config=workflow.config)
    except HirakuError:
        return False
    return True


def _job_scripts(root: Any, limit: int = 20) -> list[str]:
    """Tracked job scripts that show how jobs run on this site."""
    try:
        listed = subprocess.run(
            ["git", "ls-files", "*.sbatch", "*.slurm"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        return []
    return [path for path in listed if "archive/" not in path][:limit]


def _sequence(record: Record) -> int:
    value = getattr(record, "sequence", 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


WORKFLOW = Research
