"""Collaborative research: the agent does each step, the user reviews it."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from hiraku.errors import NotReady, SubmissionRejected
from hiraku.git import GitRepository
from hiraku.record import Record, load_record
from hiraku.workflow import Instruction, Workflow, reply

Step = Literal["draft", "implement", "launch", "analyze"]
Verdict = Literal["supports", "refutes", "inconclusive"]
# The step a review offers after each step; after analyze, the next experiment.
NEXT: dict[str, str] = {
    "draft": "implement",
    "implement": "launch",
    "launch": "analyze",
    "analyze": "draft",
}


class State(BaseModel):
    task: str
    entrypoint: str
    rounds: int
    step: Step = "draft"
    # The result of the step under review; None while the agent works on it.
    review: dict[str, Any] | None = None
    experiment: str | None = None
    feedback: str | None = None
    results: list[dict[str, Any]] = Field(default_factory=list)
    finished: bool = False


class Collaborative(Workflow):
    """Draft, implement, launch, and analyze experiments with the user.

    Each step is a pair: the agent does the work and reports it with a reply
    such as ``drafted``, which Hiraku checks and records; then the user reviews
    the result and chooses the next step, such as ``implement``, or ``revise``
    or ``finish``. Hiraku writes the experiment records, checks commits and run
    attribution, and stops offering new experiments after ``rounds``.
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
    ) -> State:
        """Start collaborative research on a task record."""
        task = task or self.config.active_record_id
        if task is None:
            raise SubmissionRejected("No active record; pass the task record ID")
        # Raises if the task does not declare the entrypoint, rejecting the start.
        load_record(task, config=self.config).entrypoint(entrypoint, config=self.config)
        return State(task=task, entrypoint=entrypoint, rounds=rounds)

    def next(self, state: State) -> Instruction:
        if state.finished:
            return Instruction(
                self.markdown.section("## Done"),
                context={"task": state.task, "results": state.results},
                final=True,
            )
        if state.review is not None:
            following = NEXT[state.step]
            more = following != "draft" or len(state.results) < state.rounds
            steps = [getattr(self, following)] if more else []
            return Instruction(
                self.markdown.section("## Review"),
                context={
                    "step": state.step,
                    "result": state.review,
                    "rounds_left": state.rounds - len(state.results),
                },
                replies=[*steps, self.revise, self.finish],
                requires_user_response=True,
            )
        reports = {
            "draft": self.drafted,
            "implement": self.implemented,
            "launch": self.launched,
            "analyze": self.analyzed,
        }
        text = self.markdown.section(f"## {state.step.title()}")
        return Instruction(
            f"{text}\n\n{self.rules}" if self.rules else text,
            context=self._context(state),
            replies=[reports[state.step], self.finish],
        )

    # Steps: the user's choice at a review -----------------------------------------

    @reply
    def draft(self, state: State) -> None:
        """Draft the next experiment."""
        state.experiment = None
        self._goto(state, "draft")

    @reply
    def implement(self, state: State) -> None:
        """Implement the drafted experiment."""
        self._goto(state, "implement")

    @reply
    def launch(self, state: State) -> None:
        """Launch the implemented experiment."""
        self._goto(state, "launch")

    @reply
    def analyze(self, state: State) -> None:
        """Analyze the experiment's runs once they have metrics."""
        self._goto(state, "analyze")

    @reply
    def revise(
        self,
        state: State,
        feedback: Annotated[str, Field(description="What to change in this step.")],
    ) -> None:
        """Redo the reviewed step with the user's feedback."""
        state.feedback = feedback
        state.review = None

    @reply
    def finish(self, state: State) -> None:
        """End the session and summarize the results."""
        state.finished = True

    # Reports: the agent's results, checked and recorded --------------------------

    @reply
    def drafted(
        self,
        state: State,
        record_id: Annotated[
            str, Field(description="Short ID of the experiment record.")
        ],
        summary: Annotated[str, Field(description="The experiment in one line.")],
        hypothesis: Annotated[str, Field(description="What the experiment tests.")],
        change: Annotated[str, Field(description="The planned code change.")],
    ) -> None:
        """Submit the drafted experiment."""
        body = f"## Change\n\n{change.strip()}\n"
        if state.experiment is None:
            record = Record.create(
                record_id,
                parent_id=state.task,
                type="experiment",
                status="drafted",
                summary=summary,
                hypothesis=hypothesis,
                markdown=body,
                activate=False,
                config=self.config,
            )
        elif record_id != state.experiment:
            raise SubmissionRejected(
                f"Revise the draft in place: reply with record_id {state.experiment!r}"
            )
        else:
            record = self._experiment(state)
            record.summary = summary
            record.hypothesis = hypothesis
            record.markdown = body
            record.save()
        state.experiment = record.id
        state.review = {
            "record_id": record.id,
            "summary": summary,
            "hypothesis": hypothesis,
            "change": change,
        }

    @reply
    def implemented(
        self,
        state: State,
        commit: Annotated[str, Field(description="Commit with the change.")],
        notes: Annotated[str, Field(description="What changed, briefly.")],
    ) -> None:
        """Submit the commit that implements the experiment."""
        sha = GitRepository(self.config).resolve(commit)
        record = self._experiment(state)
        record.commit = sha
        record.status = "implemented"
        record.save()
        state.review = {"commit": sha, "notes": notes}

    @reply
    def launched(self, state: State) -> None:
        """Report that the experiment's run is tracked."""
        record = self._experiment(state)
        runs = record.runs(config=self.config)
        if not runs:
            raise NotReady(
                f"No run is attributed to {record.id!r} yet; reply again once the "
                "launch is tracked"
            )
        if any(run.commit not in (None, record.commit) for run in runs):
            raise SubmissionRejected(
                f"A run of {record.id!r} used another commit than {record.commit}"
            )
        record.status = "launched"
        record.save()
        state.review = {"runs": _runs(record, self)}

    @reply
    def analyzed(
        self,
        state: State,
        verdict: Annotated[
            Verdict, Field(description="Whether the runs support the hypothesis.")
        ],
        conclusion: Annotated[str, Field(description="A few sentences.")],
    ) -> None:
        """Submit the verdict and conclusion of the experiment."""
        record = self._experiment(state)
        record.status = "analyzed"
        record.verdict = verdict
        record.conclusion = conclusion
        record.save()
        result = {"record_id": record.id, "verdict": verdict}
        # A revised analysis replaces the earlier result of the same experiment.
        state.results = [
            *(item for item in state.results if item["record_id"] != record.id),
            result,
        ]
        state.review = {**result, "conclusion": conclusion}

    # Helpers ----------------------------------------------------------------------

    def _goto(self, state: State, step: Step) -> None:
        state.step = step
        state.feedback = None
        state.review = None

    def _context(self, state: State) -> dict[str, Any]:
        context: dict[str, Any] = {"task": state.task, "feedback": state.feedback}
        if state.step == "draft":
            context["previous"] = state.results
            if state.experiment is not None:
                context["revise"] = state.experiment
            return context
        record = self._experiment(state)
        context["experiment"] = {
            "record_id": record.id,
            "summary": getattr(record, "summary", None),
            "hypothesis": getattr(record, "hypothesis", None),
            "change": record.markdown.strip(),
        }
        if state.step == "launch":
            context["command"] = (
                f"hiraku launch {state.entrypoint} --record {record.id} "
                f"--commit {record.commit}"
            )
        if state.step == "analyze":
            runs = _runs(record, self)
            if not any(run["metrics"] for run in runs):
                raise NotReady(
                    f"The runs of {record.id!r} have no metrics yet; analyze once "
                    "they finish"
                )
            context["runs"] = runs
            context["previous"] = state.results
        return context

    def _experiment(self, state: State) -> Record:
        if state.experiment is None:
            raise SubmissionRejected("No experiment is drafted yet")
        return load_record(state.experiment, config=self.config)


def _runs(record: Record, workflow: Workflow) -> list[dict[str, Any]]:
    return [
        {"run_id": run.id, "metrics": dict(run.metrics)}
        for run in record.runs(config=workflow.config)
    ]


WORKFLOW = Collaborative
