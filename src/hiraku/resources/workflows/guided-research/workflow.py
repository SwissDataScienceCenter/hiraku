"""Guided research: one experiment at a time, with the user deciding each step."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from hiraku.errors import NotReady, SubmissionRejected
from hiraku.record import Record, load_record, update_record
from hiraku.research import (
    Evidence,
    assess_run,
    comparison,
    find_run,
    improves,
    resolve_task,
)
from hiraku.workflow import Completion, Instruction, Session, Workflow, tool


class Launch(BaseModel):
    """The launch configuration the user approved."""

    record_id: str
    commit: str
    entrypoint: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Outcome(BaseModel):
    """What became of one selected experiment."""

    record_id: str
    outcome: Literal["improved", "not_improved", "first_result", "crashed", "skipped"]
    run_id: str | None = None
    value: float | None = None
    note: str | None = None


class GuidedSession(Session):
    task: str
    candidates: int
    entrypoint: str
    metric: str
    direction: Literal["minimize", "maximize"]
    reference: Outcome | None = None
    candidate_ids: list[str] = Field(default_factory=list)
    proposed_ids: list[str] = Field(default_factory=list)
    feedback: str | None = None
    selected: str | None = None
    approved: Launch | None = None
    outcomes: list[Outcome] = Field(default_factory=list)


class GuidedResearch(Workflow):
    """Investigate a task together with the user, one experiment at a time.

    The agent proposes hypotheses; the user chooses one, approves its launch,
    and decides after each result whether to continue. Hiraku checks the
    tracked run against the approved launch before reporting a result.
    """

    success_status: str = "finished"
    """Run status that counts as a successful execution; set in front matter."""

    @tool
    def start(
        self,
        task: Annotated[
            str | None,
            Field(description="Task record ID; defaults to the active record's task."),
        ] = None,
        candidates: Annotated[
            int, Field(gt=0, description="Hypotheses to propose in each round.")
        ] = 3,
        entrypoint: Annotated[
            str | None,
            Field(
                description="Entrypoint to launch; required if the task has several."
            ),
        ] = None,
        metric: Annotated[
            str | None,
            Field(description="Declared metric to compare; required if several."),
        ] = None,
    ) -> Instruction:
        """Start guided research on a task record."""
        record = resolve_task(self.config, task)
        chosen_entrypoint, chosen_metric, direction = comparison(
            record, self.config, entrypoint, metric
        )
        session = GuidedSession(
            task=record.id,
            candidates=candidates,
            entrypoint=chosen_entrypoint,
            metric=chosen_metric,
            direction=direction,
        )
        return self._propose(session)

    @tool
    def proposed(
        self,
        session: GuidedSession,
        record_ids: Annotated[
            list[str], Field(description="IDs of the new candidate records.")
        ],
    ) -> Instruction:
        """Submit the newly created candidate experiment records."""
        if len(record_ids) != session.candidates or len(set(record_ids)) != len(
            record_ids
        ):
            raise SubmissionRejected(
                f"Submit {session.candidates} distinct new candidate records"
            )
        records = [self._candidate(session, record_id) for record_id in record_ids]
        session.candidate_ids = list(record_ids)
        session.proposed_ids.extend(record_ids)
        session.feedback = None
        return self.instruction(
            session,
            self.prompt("#choose"),
            context={"candidates": [_summary(record) for record in records]},
            requires_user_response=True,
            replies=[self.select, self.revise, self.finish],
        )

    @tool
    def revise(
        self,
        session: GuidedSession,
        feedback: Annotated[str, Field(description="What the user wants changed.")],
    ) -> Instruction:
        """Ask for new candidates instead of choosing one."""
        session.feedback = feedback
        return self._propose(session)

    @tool
    def select(
        self,
        session: GuidedSession,
        record_id: Annotated[str, Field(description="The candidate the user chose.")],
    ) -> Instruction:
        """Select the candidate the user chose."""
        if record_id not in session.candidate_ids:
            raise SubmissionRejected(
                f"{record_id!r} is not a candidate: {', '.join(session.candidate_ids)}"
            )
        session.selected = record_id
        session.candidate_ids = []
        return self.instruction(
            session,
            self.prompt("#implement"),
            context={"record_id": record_id},
            replies=[self.implemented, self.skip],
        )

    @tool
    def implemented(self, session: GuidedSession) -> Instruction:
        """Report that the selected candidate is implemented and committed."""
        launch = self._launch(session)
        return self.instruction(
            session,
            self.prompt("#approve"),
            context={**launch.model_dump(), "command": _command(launch)},
            requires_user_response=True,
            replies=[self.approve, self.skip],
        )

    @tool
    def approve(self, session: GuidedSession) -> Instruction:
        """Record the user's approval of the presented launch."""
        session.approved = self._launch(session)
        return self.instruction(
            session,
            self.prompt("#launch"),
            context={
                **session.approved.model_dump(),
                "command": _command(session.approved),
            },
            replies=[self.submit_run, self.skip],
        )

    @tool
    def submit_run(
        self,
        session: GuidedSession,
        run_id: Annotated[str, Field(description="Tracked run ID of the launch.")],
    ) -> Instruction:
        """Submit the tracked run of the approved launch."""
        approved = session.approved
        if approved is None:
            raise SubmissionRejected("No launch is approved")
        if self._launch(session) != approved:
            # Approval is prospective: a changed configuration needs a new one.
            session.approved = None
            return self.implemented(session)
        task = load_record(session.task, config=self.config)
        run = find_run(task, self.config, run_id)
        if run is None:
            raise NotReady(
                f"Run {run_id!r} is not visible yet; check again once it finished"
            )
        record = load_record(approved.record_id, config=self.config)
        evidence = assess_run(
            run,
            record=record,
            entrypoint=approved.entrypoint,
            metric=session.metric,
            success_status=self.success_status,
        )
        for name, value in approved.arguments.items():
            tracked = run.params.get(name, run.params.get(name.replace("-", "_")))
            if tracked is not None and str(tracked) != str(value):
                evidence = Evidence(
                    "invalid", f"Run {run_id!r} tracked {name}={tracked!r}"
                )
        if evidence.outcome == "invalid":
            raise SubmissionRejected(
                f"{evidence.message}; it is not the approved launch. Launch the "
                "approved command, or skip this experiment."
            )
        outcome = self._outcome(session, approved.record_id, run.id, evidence)
        update_record(
            approved.record_id,
            changes={"assessment": outcome.model_dump(exclude_none=True)},
            config=self.config,
        )
        session.outcomes.append(outcome)
        session.approved = None
        return self.instruction(
            session,
            self.prompt("#discuss"),
            context={
                "result": outcome.model_dump(),
                "reference": session.reference.model_dump()
                if session.reference
                else None,
                "metric": session.metric,
                "direction": session.direction,
            },
            requires_user_response=True,
            replies=[self.next_round, self.finish],
        )

    @tool
    def skip(
        self,
        session: GuidedSession,
        reason: Annotated[str, Field(description="Why the experiment is skipped.")],
    ) -> Instruction:
        """Skip the selected experiment without a result."""
        if session.selected is None:
            raise SubmissionRejected("No experiment is selected")
        session.outcomes.append(
            Outcome(record_id=session.selected, outcome="skipped", note=reason)
        )
        session.selected = None
        session.approved = None
        return self._propose(session)

    @tool
    def next_round(self, session: GuidedSession) -> Instruction:
        """Continue with new candidates after recording the conclusion."""
        self._require_conclusion(session)
        session.selected = None
        return self._propose(session)

    @tool
    def finish(self, session: GuidedSession) -> Completion:
        """End the session and summarize the results."""
        self._require_conclusion(session)
        return self.completion(
            session,
            {
                "task": session.task,
                "metric": session.metric,
                "best": session.reference.model_dump() if session.reference else None,
                "outcomes": [item.model_dump() for item in session.outcomes],
            },
        )

    def _propose(self, session: GuidedSession) -> Instruction:
        session.candidate_ids = []
        return self.instruction(
            session,
            self.prompt("#propose"),
            context={
                "task": session.task,
                "count": session.candidates,
                "metric": session.metric,
                "direction": session.direction,
                "best": session.reference.model_dump() if session.reference else None,
                "previous": [item.model_dump() for item in session.outcomes],
                "feedback": session.feedback,
            },
            replies=[self.proposed, self.finish],
        )

    def _candidate(self, session: GuidedSession, record_id: str) -> Record:
        if record_id in session.proposed_ids:
            raise SubmissionRejected(f"Record {record_id!r} was already proposed")
        record = load_record(record_id, config=self.config)
        if record.parent_id != session.task or record.type != "experiment":
            raise SubmissionRejected(
                f"Record {record_id!r} must be an experiment under {session.task!r}"
            )
        return record

    def _launch(self, session: GuidedSession) -> Launch:
        if session.selected is None:
            raise SubmissionRejected("No experiment is selected")
        record = load_record(session.selected, config=self.config)
        if record.commit is None:
            raise SubmissionRejected(
                f"Bind the implementation commit to {record.id!r} with record_update"
            )
        entrypoint = record.entrypoint(session.entrypoint, config=self.config)
        return Launch(
            record_id=record.id,
            commit=record.commit,
            entrypoint=session.entrypoint,
            arguments=dict(entrypoint.args),
        )

    def _outcome(
        self, session: GuidedSession, record_id: str, run_id: str, evidence: Evidence
    ) -> Outcome:
        if evidence.outcome == "failed":
            return Outcome(
                record_id=record_id,
                outcome="crashed",
                run_id=run_id,
                note=evidence.message,
            )
        reference = session.reference
        if reference is None or reference.value is None:
            kind = "first_result"
        elif improves(evidence.value or 0.0, reference.value, session.direction):
            kind = "improved"
        else:
            kind = "not_improved"
        outcome = Outcome(
            record_id=record_id, outcome=kind, run_id=run_id, value=evidence.value
        )
        if kind in {"first_result", "improved"}:
            session.reference = outcome
        return outcome

    def _require_conclusion(self, session: GuidedSession) -> None:
        """Require a conclusion once the selected experiment has a result."""
        last = session.outcomes[-1] if session.outcomes else None
        if (
            session.selected is None
            or last is None
            or last.record_id != session.selected
            or last.outcome == "skipped"
        ):
            return
        fields = load_record(session.selected, config=self.config).to_dict()
        if not str(fields.get("conclusion") or "").strip():
            raise SubmissionRejected(
                f"Write the conclusion agreed with the user on {session.selected!r}"
            )


def _command(launch: Launch) -> str:
    return (
        f"hiraku launch {launch.entrypoint} --record {launch.record_id} "
        f"--commit {launch.commit}"
    )


def _summary(record: Record) -> dict[str, Any]:
    values = record.to_dict()
    return {
        "id": record.id,
        **{
            key: values[key]
            for key in ("summary", "hypothesis", "rationale")
            if key in values
        },
    }


WORKFLOW = GuidedResearch
