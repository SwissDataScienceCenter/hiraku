"""Collaborative research: the agent does each step, the user reviews it."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from hiraku.errors import NotReady, SubmissionRejected
from hiraku.git import GitRepository
from hiraku.record import Record, load_record
from hiraku.workflow import Completion, Instruction, Session, Workflow, reply

Step = Literal["draft", "implement", "launch", "analyze"]
Verdict = Literal["supports", "refutes", "inconclusive"]
# The step a review offers after each step; after analyze, the next experiment.
NEXT: dict[str, str] = {
    "draft": "implement",
    "implement": "launch",
    "launch": "analyze",
    "analyze": "draft",
}


class CollaborativeSession(Session):
    task: str
    entrypoint: str
    rounds: int
    step: Step = "draft"
    experiment: str | None = None
    feedback: str | None = None
    results: list[dict[str, Any]] = Field(default_factory=list)


class Collaborative(Workflow):
    """Draft, implement, launch, and analyze experiments with the user.

    Each step is a pair: the agent does the work and reports it with a reply
    such as ``drafted``, which Hiraku checks and records; then the user reviews
    the result and chooses the next step, such as ``implement``, or ``revise``
    or ``finish``. Hiraku writes the experiment records, checks commits and run
    attribution, and stops offering new experiments after ``rounds``.
    """

    def start(
        self,
        task: Annotated[
            str | None, Field(description="Task record; defaults to the active record.")
        ] = None,
        entrypoint: Annotated[
            str, Field(description="Entrypoint of the task to launch.")
        ] = "train",
        rounds: Annotated[int, Field(gt=0, description="Most experiments to run.")] = 3,
    ) -> Instruction:
        """Start collaborative research on a task record."""
        task = task or self.config.active_record_id
        if task is None:
            raise SubmissionRejected("No active record; pass the task record ID")
        # Raises if the task does not declare the entrypoint, rejecting the start.
        load_record(task, config=self.config).entrypoint(entrypoint, config=self.config)
        session = CollaborativeSession(task=task, entrypoint=entrypoint, rounds=rounds)
        return self._work(session)

    # Steps: the user's choice at a review -----------------------------------------

    @reply
    def draft(self, session: CollaborativeSession) -> Instruction:
        """Draft the next experiment."""
        session.experiment = None
        return self._goto(session, "draft")

    @reply
    def implement(self, session: CollaborativeSession) -> Instruction:
        """Implement the drafted experiment."""
        return self._goto(session, "implement")

    @reply
    def launch(self, session: CollaborativeSession) -> Instruction:
        """Launch the implemented experiment."""
        return self._goto(session, "launch")

    @reply
    def analyze(self, session: CollaborativeSession) -> Instruction:
        """Analyze the experiment's runs once they have metrics."""
        return self._goto(session, "analyze")

    @reply
    def revise(
        self,
        session: CollaborativeSession,
        feedback: Annotated[str, Field(description="What to change in this step.")],
    ) -> Instruction:
        """Redo the reviewed step with the user's feedback."""
        session.feedback = feedback
        return self._work(session)

    @reply
    def finish(self, session: CollaborativeSession) -> Completion:
        """End the session and summarize the results."""
        return self.completion(
            session, {"task": session.task, "results": session.results}
        )

    # Reports: the agent's results, checked and recorded --------------------------

    @reply
    def drafted(
        self,
        session: CollaborativeSession,
        record_id: Annotated[
            str, Field(description="Short ID of the experiment record.")
        ],
        summary: Annotated[str, Field(description="The experiment in one line.")],
        hypothesis: Annotated[str, Field(description="What the experiment tests.")],
        change: Annotated[str, Field(description="The planned code change.")],
    ) -> Instruction:
        """Submit the drafted experiment."""
        body = f"## Change\n\n{change.strip()}\n"
        if session.experiment is None:
            record = Record.create(
                record_id,
                parent_id=session.task,
                type="experiment",
                status="drafted",
                summary=summary,
                hypothesis=hypothesis,
                markdown=body,
                activate=False,
                config=self.config,
            )
        elif record_id != session.experiment:
            raise SubmissionRejected(
                f"Revise the draft in place: reply with record_id "
                f"{session.experiment!r}"
            )
        else:
            record = self._experiment(session)
            record.summary = summary
            record.hypothesis = hypothesis
            record.markdown = body
            record.save()
        session.experiment = record.id
        return self._review(
            session,
            {
                "record_id": record.id,
                "summary": summary,
                "hypothesis": hypothesis,
                "change": change,
            },
        )

    @reply
    def implemented(
        self,
        session: CollaborativeSession,
        commit: Annotated[str, Field(description="Commit with the change.")],
        notes: Annotated[str, Field(description="What changed, briefly.")],
    ) -> Instruction:
        """Submit the commit that implements the experiment."""
        sha = GitRepository(self.config).resolve(commit)
        record = self._experiment(session)
        record.commit = sha
        record.status = "implemented"
        record.save()
        return self._review(session, {"commit": sha, "notes": notes})

    @reply
    def launched(self, session: CollaborativeSession) -> Instruction:
        """Report that the experiment's run is tracked."""
        record = self._experiment(session)
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
        return self._review(session, {"runs": _runs(record, self)})

    @reply
    def analyzed(
        self,
        session: CollaborativeSession,
        verdict: Annotated[
            Verdict, Field(description="Whether the runs support the hypothesis.")
        ],
        conclusion: Annotated[str, Field(description="A few sentences.")],
    ) -> Instruction:
        """Submit the verdict and conclusion of the experiment."""
        record = self._experiment(session)
        record.status = "analyzed"
        record.verdict = verdict
        record.conclusion = conclusion
        record.save()
        result = {"record_id": record.id, "verdict": verdict}
        # A revised analysis replaces the earlier result of the same experiment.
        session.results = [
            *(item for item in session.results if item["record_id"] != record.id),
            result,
        ]
        return self._review(session, {**result, "conclusion": conclusion})

    # Helpers ----------------------------------------------------------------------

    def _goto(self, session: CollaborativeSession, step: Step) -> Instruction:
        session.step = step
        session.feedback = None
        return self._work(session)

    def _work(self, session: CollaborativeSession) -> Instruction:
        reports = {
            "draft": self.drafted,
            "implement": self.implemented,
            "launch": self.launched,
            "analyze": self.analyzed,
        }
        return self.instruction(
            session,
            self.section(session.step),
            context=self._context(session),
            replies=[reports[session.step], self.finish],
        )

    def _review(
        self, session: CollaborativeSession, result: dict[str, Any]
    ) -> Instruction:
        following = NEXT[session.step]
        more = following != "draft" or len(session.results) < session.rounds
        steps = [getattr(self, following)] if more else []
        return self.instruction(
            session,
            self.section("review"),
            context={
                "step": session.step,
                "result": result,
                "rounds_left": session.rounds - len(session.results),
            },
            requires_user_response=True,
            replies=[*steps, self.revise, self.finish],
        )

    def _context(self, session: CollaborativeSession) -> dict[str, Any]:
        context: dict[str, Any] = {"task": session.task, "feedback": session.feedback}
        if session.step == "draft":
            context["previous"] = session.results
            if session.experiment is not None:
                context["revise"] = session.experiment
            return context
        record = self._experiment(session)
        context["experiment"] = {
            "record_id": record.id,
            "summary": getattr(record, "summary", None),
            "hypothesis": getattr(record, "hypothesis", None),
            "change": record.markdown.strip(),
        }
        if session.step == "launch":
            context["command"] = (
                f"hiraku launch {session.entrypoint} --record {record.id} "
                f"--commit {record.commit}"
            )
        if session.step == "analyze":
            runs = _runs(record, self)
            if not any(run["metrics"] for run in runs):
                raise NotReady(
                    f"The runs of {record.id!r} have no metrics yet; analyze once "
                    "they finish"
                )
            context["runs"] = runs
            context["previous"] = session.results
        return context

    def _experiment(self, session: CollaborativeSession) -> Record:
        if session.experiment is None:
            raise SubmissionRejected("No experiment is drafted yet")
        return load_record(session.experiment, config=self.config)


def _runs(record: Record, workflow: Workflow) -> list[dict[str, Any]]:
    return [
        {"run_id": run.id, "metrics": dict(run.metrics)}
        for run in record.runs(config=workflow.config)
    ]


WORKFLOW = Collaborative
