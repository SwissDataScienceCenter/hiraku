"""Collaborative research: the agent does each step and the user approves it."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from hiraku.errors import EventRejected, NotReady
from hiraku.git import GitRepository
from hiraku.record import Record, load_record
from hiraku.workflow import Instruction, Workflow, event, state

Verdict = Literal["supports", "refutes", "inconclusive"]
STEPS = ["draft", "implement", "launch", "analyze"]


class Experiments(BaseModel):
    task: str
    entrypoint: str
    rounds: int
    experiment: str | None = None
    results: list[dict[str, Any]] = Field(default_factory=list)


class Collaborative(Workflow):
    """Draft, implement, launch, and analyze experiments with the user.

    The agent works through the four steps of each experiment and gets the
    user's approval before it reports a step with an event such as ``drafted``,
    which Hiraku checks and records. Hiraku writes the experiment records,
    checks commits and run attribution, and stops after ``rounds`` experiments.
    """

    # Setting: rules for every step, added to each step's instruction.
    rules: str = ""

    def start(
        self,
        task: Annotated[
            str | None, Field(description="Task record; defaults to the active record.")
        ] = None,
        entrypoint: Annotated[
            str, Field(description="Entrypoint of the task to launch.")
        ] = "train",
        rounds: Annotated[int, Field(gt=0, description="Most experiments to run.")] = 3,
    ) -> Experiments:
        """Start collaborative research on a task record."""
        task = task or self.config.active_record_id
        if task is None:
            raise EventRejected("No active record; pass the task record ID")
        # Raises if the task does not declare the entrypoint, rejecting the start.
        load_record(task, config=self.config).entrypoint(entrypoint, config=self.config)
        return Experiments(task=task, entrypoint=entrypoint, rounds=rounds)

    # States: one per step of an experiment ------------------------------------------

    @state("draft", start=True)
    def drafting(self, experiments: Experiments) -> Instruction:
        context = {"task": experiments.task, "previous": experiments.results}
        return self._step("## Draft", context)

    @state("implement")
    def implementing(self, experiments: Experiments) -> Instruction:
        return self._step("## Implement", self._experiment_context(experiments))

    @state("launch")
    def launching(self, experiments: Experiments) -> Instruction:
        context = self._experiment_context(experiments)
        record = self._experiment(experiments)
        context["command"] = (
            f"hiraku launch {experiments.entrypoint} --record {record.id} "
            f"--commit {record.commit}"
        )
        return self._step("## Launch", context)

    @state("analyze")
    def analyzing(self, experiments: Experiments) -> Instruction:
        record = self._experiment(experiments)
        runs = _runs(record, self)
        if not any(run["metrics"] for run in runs):
            raise NotReady(
                f"The runs of {record.id!r} have no metrics yet; analyze once they "
                "finish"
            )
        context = {
            **self._experiment_context(experiments),
            "runs": runs,
            "previous": experiments.results,
        }
        return self._step("## Analyze", context)

    @state("finished", final=True)
    def summarizing(self, experiments: Experiments) -> Instruction:
        return Instruction(
            self.markdown.section("## Done"),
            context={"task": experiments.task, "results": experiments.results},
        )

    # Events: the agent's approved results, checked and recorded ----------------------

    @event(source="draft", dest="implement")
    def drafted(
        self,
        experiments: Experiments,
        record_id: Annotated[
            str, Field(description="Short ID of the experiment record.")
        ],
        summary: Annotated[str, Field(description="The experiment in one line.")],
        hypothesis: Annotated[str, Field(description="What the experiment tests.")],
        change: Annotated[str, Field(description="The planned code change.")],
    ) -> None:
        """Submit the drafted experiment the user approved."""
        record = Record.create(
            record_id,
            parent_id=experiments.task,
            type="experiment",
            status="drafted",
            summary=summary,
            hypothesis=hypothesis,
            markdown=f"## Change\n\n{change.strip()}\n",
            activate=False,
            config=self.config,
        )
        experiments.experiment = record.id

    @event(source="implement", dest="launch")
    def implemented(
        self,
        experiments: Experiments,
        commit: Annotated[str, Field(description="Commit with the change.")],
        notes: Annotated[str, Field(description="What changed, briefly.")],
    ) -> None:
        """Submit the commit that implements the experiment."""
        sha = GitRepository(self.config).resolve(commit)
        record = self._experiment(experiments)
        record.commit = sha
        record.status = "implemented"
        record.save()

    @event(source="launch", dest="analyze")
    def launched(self, experiments: Experiments) -> None:
        """Report that the experiment's run is tracked."""
        record = self._experiment(experiments)
        runs = record.runs(config=self.config)
        if not runs:
            raise NotReady(
                f"No run is attributed to {record.id!r} yet; send the event again "
                "once the launch is tracked"
            )
        if any(run.commit not in (None, record.commit) for run in runs):
            raise EventRejected(
                f"A run of {record.id!r} used another commit than {record.commit}"
            )
        record.status = "launched"
        record.save()

    @event(source="analyze", dest=["draft", "finished"])
    def analyzed(
        self,
        experiments: Experiments,
        verdict: Annotated[
            Verdict, Field(description="Whether the runs support the hypothesis.")
        ],
        conclusion: Annotated[str, Field(description="A few sentences.")],
    ) -> str:
        """Submit the verdict and conclusion of the experiment."""
        record = self._experiment(experiments)
        record.status = "analyzed"
        record.verdict = verdict
        record.conclusion = conclusion
        record.save()
        experiments.results.append({"record_id": record.id, "verdict": verdict})
        experiments.experiment = None
        return "draft" if len(experiments.results) < experiments.rounds else "finished"

    @event(source=STEPS, dest="finished")
    def finish(self, experiments: Experiments) -> None:
        """End the session early and summarize the results."""

    # Helpers ----------------------------------------------------------------------

    def _step(self, section: str, context: dict[str, Any]) -> Instruction:
        text = self.markdown.section(section)
        return Instruction(
            f"{text}\n\n{self.rules}" if self.rules else text, context=context
        )

    def _experiment_context(self, experiments: Experiments) -> dict[str, Any]:
        record = self._experiment(experiments)
        return {
            "task": experiments.task,
            "experiment": {
                "record_id": record.id,
                "summary": getattr(record, "summary", None),
                "hypothesis": getattr(record, "hypothesis", None),
                "change": record.markdown.strip(),
                "commit": record.commit,
            },
        }

    def _experiment(self, experiments: Experiments) -> Record:
        if experiments.experiment is None:
            raise EventRejected("No experiment is drafted yet")
        return load_record(experiments.experiment, config=self.config)


def _runs(record: Record, workflow: Workflow) -> list[dict[str, Any]]:
    return [
        {"run_id": run.id, "metrics": dict(run.metrics)}
        for run in record.runs(config=workflow.config)
    ]


WORKFLOW = Collaborative
