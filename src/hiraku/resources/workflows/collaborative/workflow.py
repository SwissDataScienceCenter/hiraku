"""Collaborative research: the agent works, the user decides at gates."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from hiraku.errors import NotReady, SubmissionRejected
from hiraku.record import Record, load_record
from hiraku.workflow import Completion, Instruction, Session, Workflow, reply


class CollaborativeSession(Session):
    task: str
    entrypoint: str
    rounds: int
    experiment: str | None = None
    feedback: str | None = None
    results: list[dict[str, Any]] = Field(default_factory=list)


class Collaborative(Workflow):
    """Run experiments on a task together with the user, one at a time.

    The agent proposes and carries out each experiment; the user approves it
    before any work and decides after each result whether to continue.
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
        return self._propose(session)

    @reply
    def proposed(
        self,
        session: CollaborativeSession,
        record_id: Annotated[str, Field(description="The new experiment record.")],
    ) -> Instruction:
        """Submit the proposed experiment record."""
        record = load_record(record_id, config=self.config)
        if record.parent_id != session.task or record.type != "experiment":
            raise SubmissionRejected(
                f"Record {record_id!r} must be an experiment under {session.task!r}"
            )
        session.experiment = record_id
        return self.instruction(
            session,
            self.section("approve"),
            context=_summary(record),
            requires_user_response=True,
            replies=[self.approve, self.revise, self.finish],
        )

    @reply
    def revise(
        self,
        session: CollaborativeSession,
        feedback: Annotated[str, Field(description="What the user wants changed.")],
    ) -> Instruction:
        """Propose a different experiment, following the user's feedback."""
        session.feedback = feedback
        return self._propose(session)

    @reply
    def approve(self, session: CollaborativeSession) -> Instruction:
        """Record the user's approval of the proposed experiment."""
        command = f"hiraku launch {session.entrypoint} --record {session.experiment}"
        return self.instruction(
            session,
            self.section("run"),
            context={"record_id": session.experiment, "command": command},
            replies=[self.submit_run, self.finish],
        )

    @reply
    def submit_run(
        self,
        session: CollaborativeSession,
        run_id: Annotated[str, Field(description="Tracked run of the experiment.")],
    ) -> Instruction:
        """Submit the tracked run of the approved experiment."""
        record = load_record(str(session.experiment), config=self.config)
        run = next(
            (item for item in record.runs(config=self.config) if item.id == run_id),
            None,
        )
        if run is None:
            raise NotReady(
                f"No run {run_id!r} is attributed to {record.id!r} yet; check again "
                "once the launch has finished"
            )
        session.results.append(
            {"record_id": record.id, "run_id": run.id, "metrics": dict(run.metrics)}
        )
        more = len(session.results) < session.rounds
        return self.instruction(
            session,
            self.section("discuss"),
            context={
                "result": session.results[-1],
                "rounds_left": session.rounds - len(session.results),
            },
            requires_user_response=True,
            replies=[self.next_round, self.finish] if more else [self.finish],
        )

    @reply
    def next_round(self, session: CollaborativeSession) -> Instruction:
        """Continue with the next experiment."""
        session.experiment = None
        session.feedback = None
        return self._propose(session)

    @reply
    def finish(self, session: CollaborativeSession) -> Completion:
        """End the session and summarize the results."""
        return self.completion(
            session, {"task": session.task, "results": session.results}
        )

    def _propose(self, session: CollaborativeSession) -> Instruction:
        return self.instruction(
            session,
            self.section("propose"),
            context={
                "task": session.task,
                "previous": session.results,
                "feedback": session.feedback,
            },
            replies=[self.proposed, self.finish],
        )


def _summary(record: Record) -> dict[str, Any]:
    values = record.to_dict()
    return {
        "record_id": record.id,
        **{key: values[key] for key in ("summary", "hypothesis") if key in values},
    }


WORKFLOW = Collaborative
