"""Collaborative research: the agent does each step and the user approves it."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from hiraku.errors import EventRejected, HirakuError, NotReady
from hiraku.git import GitRepository
from hiraku.record import Record, load_record
from hiraku.workflows import Arguments, Instruction, State, Workflow, event, phase

Verdict = Literal["supports", "refutes", "inconclusive"]
Feedback = Annotated[str, Field(description="What the user wants changed.")]
STEPS = ["draft", "implement", "launch", "analyze"]


class Experiments(State):
    phase: str = "draft"
    task: str
    entrypoint: str
    rounds: int
    experiment: str | None = None
    feedback: str | None = None
    results: list[dict[str, Any]] = Field(default_factory=list)


class StartArguments(Arguments):
    task: Annotated[
        str | None, Field(description="Task record; defaults to the active record.")
    ] = None
    entrypoint: Annotated[
        str, Field(description="Entrypoint of the task to launch.")
    ] = "train"
    rounds: Annotated[int, Field(gt=0, description="Most experiments to run.")] = 3


class DraftedArguments(Arguments):
    record_id: Annotated[str, Field(description="Short ID of the experiment record.")]
    summary: Annotated[str, Field(description="The experiment in one line.")]
    hypothesis: Annotated[str, Field(description="What the experiment tests.")]
    change: Annotated[str, Field(description="The planned code change.")]


class ImplementedArguments(Arguments):
    commit: Annotated[str, Field(description="Commit with the change.")]


class AnalyzedArguments(Arguments):
    verdict: Annotated[
        Verdict, Field(description="Whether the runs support the hypothesis.")
    ]
    conclusion: Annotated[str, Field(description="A few sentences.")]


class FeedbackArguments(Arguments):
    feedback: Feedback


class Collaborative(Workflow):
    """Draft, implement, launch, and analyze experiments with the user.

    The agent works through the four steps of each experiment and gets the
    user's approval before it reports a step with an event such as ``drafted``,
    which Hiraku checks and records. Until then, revision events redo the step
    with the user's feedback. Hiraku writes the experiment records, checks
    commits and run attribution, and stops after ``rounds`` experiments.
    """

    rules: str = ""

    @event(source=None, dest="draft")
    def start(self, args: StartArguments) -> Experiments:
        """Start collaborative research on a task record."""
        try:
            task = args.task
            entrypoint = args.entrypoint
            rounds = args.rounds
            task = task or self.config.active_record_id
            if task is None:
                raise EventRejected("No active record; pass the task record ID")
            load_record(task, config=self.config).entrypoint(
                entrypoint, config=self.config
            )
            return Experiments(task=task, entrypoint=entrypoint, rounds=rounds)
        except EventRejected:
            raise
        except HirakuError as exc:
            raise EventRejected(str(exc)) from exc

    @phase("draft")
    def drafting(self, experiments: Experiments) -> Instruction:
        context = {"task": experiments.task, "previous": experiments.results}
        return self._step(experiments, "## Draft", context)

    @phase("implement")
    def implementing(self, experiments: Experiments) -> Instruction:
        return self._step(
            experiments, "## Implement", self._experiment_context(experiments)
        )

    @phase("launch")
    def launching(self, experiments: Experiments) -> Instruction:
        context = self._experiment_context(experiments)
        record = self._experiment(experiments)
        context["command"] = (
            f"hiraku launch {experiments.entrypoint} --record {record.id} "
            f"--commit {record.commit}"
        )
        return self._step(experiments, "## Launch", context)

    @phase("analyze")
    def analyzing(self, experiments: Experiments) -> Instruction:
        record = self._experiment(experiments)
        runs = _runs(record, self)
        if not any(run["metrics"] for run in runs):
            raise NotReady(
                f"The runs of {record.id!r} have no metrics yet; "
                "analyze once they finish"
            )
        context = {
            **self._experiment_context(experiments),
            "runs": runs,
            "previous": experiments.results,
        }
        return self._step(experiments, "## Analyze", context)

    @phase("finished", final=True)
    def summarizing(self, experiments: Experiments) -> Instruction:
        return Instruction(
            self.markdown.section("## Done"),
            context={"task": experiments.task, "results": experiments.results},
        )

    @event(source="draft", dest="implement")
    def drafted(self, experiments: Experiments, args: DraftedArguments) -> Experiments:
        """Submit the drafted experiment the user approved."""
        try:
            record_id = args.record_id
            summary = args.summary
            hypothesis = args.hypothesis
            change = args.change
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
            return experiments.model_copy(
                update={
                    "phase": "implement",
                    "experiment": record.id,
                    "feedback": None,
                }
            )
        except EventRejected:
            raise
        except HirakuError as exc:
            raise EventRejected(str(exc)) from exc

    @event(source="implement", dest="launch")
    def implemented(
        self, experiments: Experiments, args: ImplementedArguments
    ) -> Experiments:
        """Submit the commit that implements the experiment."""
        try:
            commit = args.commit
            sha = GitRepository(self.config).resolve(commit)
            record = self._experiment(experiments)
            record.commit = sha
            record.status = "implemented"
            record.save()
            return experiments.model_copy(update={"phase": "launch", "feedback": None})
        except EventRejected:
            raise
        except HirakuError as exc:
            raise EventRejected(str(exc)) from exc

    @event(source="launch", dest="analyze")
    def launched(self, experiments: Experiments) -> Experiments:
        """Report that the experiment's run is tracked."""
        try:
            record = self._experiment(experiments)
            runs = record.runs(config=self.config)
            if not runs:
                raise NotReady(
                    f"No run is attributed to {record.id!r} yet; "
                    "send the event again once the launch is tracked"
                )
            if any(run.commit not in (None, record.commit) for run in runs):
                raise EventRejected(
                    f"A run of {record.id!r} used another commit than {record.commit}"
                )
            record.status = "launched"
            record.save()
            return experiments.model_copy(update={"phase": "analyze", "feedback": None})
        except EventRejected:
            raise
        except HirakuError as exc:
            raise EventRejected(str(exc)) from exc

    @event(source="analyze", dest=["draft", "finished"])
    def analyzed(
        self, experiments: Experiments, args: AnalyzedArguments
    ) -> Experiments:
        """Submit the verdict and conclusion of the experiment."""
        try:
            verdict = args.verdict
            conclusion = args.conclusion
            record = self._experiment(experiments)
            record.status = "analyzed"
            record.verdict = verdict
            record.conclusion = conclusion
            record.save()
            experiments = experiments.model_copy(
                update={
                    "results": [
                        *experiments.results,
                        {"record_id": record.id, "verdict": verdict},
                    ]
                }
            )
            experiments = experiments.model_copy(update={"experiment": None})
            experiments = experiments.model_copy(update={"feedback": None})
            experiments = experiments.model_copy(
                update={
                    "phase": "draft"
                    if len(experiments.results) < experiments.rounds
                    else "finished"
                }
            )
            return experiments
        except EventRejected:
            raise
        except HirakuError as exc:
            raise EventRejected(str(exc)) from exc

    @event(source="draft", dest="draft")
    def revise_draft(
        self, experiments: Experiments, args: FeedbackArguments
    ) -> Experiments:
        """Redraft the experiment with the user's feedback."""
        return experiments.model_copy(update={"feedback": args.feedback})

    @event(source="implement", dest="implement")
    def revise_implementation(
        self, experiments: Experiments, args: FeedbackArguments
    ) -> Experiments:
        """Rework the implementation with the user's feedback."""
        return experiments.model_copy(update={"feedback": args.feedback})

    @event(source="launch", dest="launch")
    def revise_launch(
        self, experiments: Experiments, args: FeedbackArguments
    ) -> Experiments:
        """Change how the experiment is launched, with the user's feedback."""
        return experiments.model_copy(update={"feedback": args.feedback})

    @event(source="analyze", dest="analyze")
    def revise_analysis(
        self, experiments: Experiments, args: FeedbackArguments
    ) -> Experiments:
        """Redo the analysis with the user's feedback."""
        return experiments.model_copy(update={"feedback": args.feedback})

    @event(source=STEPS, dest="finished")
    def finish(self, experiments: Experiments) -> Experiments:
        """End the session early and summarize the results."""
        return experiments.model_copy(update={"phase": "finished"})

    def _step(
        self, experiments: Experiments, section: str, context: dict[str, Any]
    ) -> Instruction:
        text = self.markdown.section(section)
        if experiments.feedback is not None:
            context = {**context, "feedback": experiments.feedback}
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
