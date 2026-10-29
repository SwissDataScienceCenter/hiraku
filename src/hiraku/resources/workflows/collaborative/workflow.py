"""Collaborative research: the agent does each step and the user approves it."""

from __future__ import annotations

import shlex
from typing import Annotated, Any, Literal

from pydantic import Field

from hiraku.errors import EventRejected, HirakuError, NotReady
from hiraku.git import GitRepository
from hiraku.record import Record, load_record
from hiraku.run import Run
from hiraku.validation import validate_command
from hiraku.workflows.graph import State, event, node, start
from hiraku.workflows.workflow import Instruction, Workflow

Verdict = Literal["supports", "refutes", "inconclusive"]
Feedback = Annotated[str, "What the user wants changed."]
STEPS = ["draft", "implement", "launch", "analyze"]


class Experiments(State):
    node: str = "draft"
    task: str
    command: list[str] | None = None
    rounds: int
    experiment: str | None = None
    run_id: str | None = None
    feedback: str | None = None
    results: list[dict[str, Any]] = Field(default_factory=list)


class Collaborative(Workflow):
    """Draft, implement, launch, and analyze experiments with the user.

    The agent works through the four steps of each experiment and gets the
    user's approval before it reports a step with an event such as ``drafted``,
    which Hiraku checks and records. Until then, revision events redo the step
    with the user's feedback. Hiraku writes the experiment records, checks
    commits and run attribution, and stops after ``rounds`` experiments.
    """

    rules: str = ""

    @start(dest="draft")
    def start(
        self,
        *,
        task: Annotated[
            str | None, "Task record; defaults to the active record."
        ] = None,
        command: Annotated[
            list[str] | None,
            "Optional argv command; otherwise use the task's inherited command.",
        ] = None,
        rounds: Annotated[int, "Most experiments to run.", Field(gt=0)] = 3,
    ) -> Experiments:
        """Start collaborative research on a task record."""
        try:
            task = task or self.config.active_record_id
            if task is None:
                raise EventRejected("No active record; pass the task record ID")
            if command is not None:
                command = validate_command(command)
            return Experiments(task=task, command=command, rounds=rounds)
        except EventRejected:
            raise
        except HirakuError as exc:
            raise EventRejected(str(exc)) from exc

    @node("draft")
    def drafting(self, experiments: Experiments) -> Instruction:
        context = {"task": experiments.task, "previous": experiments.results}
        return self._step(experiments, "## Draft", context)

    @node("implement")
    def implementing(self, experiments: Experiments) -> Instruction:
        return self._step(
            experiments, "## Implement", self._experiment_context(experiments)
        )

    @node("launch")
    def launching(self, experiments: Experiments) -> Instruction:
        context = self._experiment_context(experiments)
        record = self._experiment(experiments)
        argv = [
            "hiraku",
            "run",
            "--record",
            record.id,
        ]
        command = experiments.command or record.resolve_command(config=self.config)
        if command:
            argv.extend(["--", *command])
        context["command"] = shlex.join(argv)
        return self._step(experiments, "## Launch", context)

    @node("analyze")
    def analyzing(self, experiments: Experiments) -> Instruction:
        record = self._experiment(experiments)
        runs = _runs(record, self, experiments.run_id)
        if not any(run.metrics for run in runs):
            raise NotReady(
                f"The runs of {record.id!r} have no metrics yet; "
                "analyze once they finish"
            )
        context = {
            **self._experiment_context(experiments),
            "runs": [{"run_id": run.id, "metrics": dict(run.metrics)} for run in runs],
            "previous": experiments.results,
        }
        return self._step(experiments, "## Analyze", context)

    @node("finished", final=True)
    def summarizing(self, experiments: Experiments) -> Instruction:
        return Instruction(
            self.markdown.section("## Done"),
            data={"task": experiments.task, "results": experiments.results},
        )

    @event(source="draft", dest="implement")
    def drafted(
        self,
        experiments: Experiments,
        *,
        record_id: Annotated[str, "Short ID of the experiment record."],
        summary: Annotated[str, "The experiment in one line."],
        hypothesis: Annotated[str, "What the experiment tests."],
        change: Annotated[str, "The planned code change."],
    ) -> Experiments:
        """Submit the drafted experiment the user approved."""
        try:
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
            experiments.node = "implement"
            experiments.experiment = record.id
            experiments.feedback = None
            return experiments
        except EventRejected:
            raise
        except HirakuError as exc:
            raise EventRejected(str(exc)) from exc

    @event(source="implement", dest="launch")
    def implemented(
        self,
        experiments: Experiments,
        *,
        commit: Annotated[str, "Commit with the change."],
    ) -> Experiments:
        """Submit the commit that implements the experiment."""
        try:
            sha = GitRepository(self.config).resolve(commit)
            record = self._experiment(experiments)
            record.commit = sha
            record.status = "implemented"
            record.save()
            experiments.node = "launch"
            experiments.feedback = None
            return experiments
        except EventRejected:
            raise
        except HirakuError as exc:
            raise EventRejected(str(exc)) from exc

    @event(source="launch", dest="analyze")
    def launched(
        self,
        experiments: Experiments,
        *,
        run_id: Annotated[str, "ID of the newly tracked run."],
    ) -> Experiments:
        """Report that the foreground execution produced a tracked run."""
        try:
            record = self._experiment(experiments)
            runs = _runs(record, self, run_id)
            if not runs:
                raise NotReady(
                    f"Run {run_id!r} is not visible in the tracking source yet"
                )
            run = runs[0]
            if run.commit not in (None, record.commit):
                raise EventRejected(
                    f"Run {run_id!r} used another commit than {record.commit}"
                )
            if not run.metrics:
                raise NotReady(f"Run {run_id!r} has no metrics yet")
            record.status = "launched"
            record.save()
            experiments.node = "analyze"
            experiments.run_id = run_id
            experiments.feedback = None
            return experiments
        except EventRejected:
            raise
        except HirakuError as exc:
            raise EventRejected(str(exc)) from exc

    @event(source="analyze", dest=["draft", "finished"])
    def analyzed(
        self,
        experiments: Experiments,
        *,
        verdict: Annotated[Verdict, "Whether the runs support the hypothesis."],
        conclusion: Annotated[str, "A few sentences."],
    ) -> Experiments:
        """Submit the verdict and conclusion of the experiment."""
        try:
            record = self._experiment(experiments)
            record.status = "analyzed"
            record.verdict = verdict
            record.conclusion = conclusion
            record.save()
            experiments.results.append({"record_id": record.id, "verdict": verdict})
            experiments.experiment = None
            experiments.feedback = None
            experiments.node = (
                "draft" if len(experiments.results) < experiments.rounds else "finished"
            )
            return experiments
        except EventRejected:
            raise
        except HirakuError as exc:
            raise EventRejected(str(exc)) from exc

    @event(source="draft", dest="draft")
    def revise_draft(
        self, experiments: Experiments, *, feedback: Feedback
    ) -> Experiments:
        """Redraft the experiment with the user's feedback."""
        experiments.feedback = feedback
        return experiments

    @event(source="implement", dest="implement")
    def revise_implementation(
        self, experiments: Experiments, *, feedback: Feedback
    ) -> Experiments:
        """Rework the implementation with the user's feedback."""
        experiments.feedback = feedback
        return experiments

    @event(source="launch", dest="launch")
    def revise_launch(
        self, experiments: Experiments, *, feedback: Feedback
    ) -> Experiments:
        """Change how the experiment is launched, with the user's feedback."""
        experiments.feedback = feedback
        return experiments

    @event(source="analyze", dest="analyze")
    def revise_analysis(
        self, experiments: Experiments, *, feedback: Feedback
    ) -> Experiments:
        """Redo the analysis with the user's feedback."""
        experiments.feedback = feedback
        return experiments

    @event(source=STEPS, dest="finished")
    def finish(self, experiments: Experiments) -> Experiments:
        """End the session early and summarize the results."""
        experiments.node = "finished"
        return experiments

    def _step(
        self, experiments: Experiments, section: str, context: dict[str, Any]
    ) -> Instruction:
        text = self.markdown.section(section)
        if experiments.feedback is not None:
            context = {**context, "feedback": experiments.feedback}
        return Instruction(
            f"{text}\n\n{self.rules}" if self.rules else text, data=context
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


def _runs(record: Record, workflow: Workflow, run_id: str | None) -> list[Run]:
    if run_id is None:
        return []
    return [run for run in record.runs(config=workflow.config) if run.id == run_id]


WORKFLOW = Collaborative
