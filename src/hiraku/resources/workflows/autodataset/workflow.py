"""Autodataset: discover and document candidate datasets in one instruction."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from hiraku.errors import SubmissionRejected
from hiraku.record import load_record
from hiraku.workflow import Completion, Instruction, Session, Workflow, reply


class DatasetSession(Session):
    task: str
    count: int
    found: list[str] = Field(default_factory=list)
    shortfall_reason: str | None = None


class Autodataset(Workflow):
    """Find datasets for a task and document each one as a source-backed record.

    The whole procedure arrives as one instruction. The harness investigates,
    talks to the user as needed, and replies once with the dataset records or
    with an explained shortfall.
    """

    def start(
        self,
        task: Annotated[
            str | None,
            Field(description="Task record ID; defaults to the active record."),
        ] = None,
        count: Annotated[
            int, Field(gt=0, description="Number of datasets to find.")
        ] = 5,
    ) -> Instruction:
        """Start dataset discovery for a task record."""
        task = task or self.config.active_record_id
        if task is None:
            raise SubmissionRejected("No active record; pass the task record ID")
        record = load_record(task, config=self.config)
        session = DatasetSession(task=record.id, count=count)
        return self._search(session)

    @reply
    def complete(
        self,
        session: DatasetSession,
        record_ids: Annotated[
            list[str], Field(description="IDs of all dataset records found.")
        ],
    ) -> Completion:
        """Submit the requested number of dataset records."""
        self._check_datasets(session, record_ids)
        if len(record_ids) != session.count:
            raise SubmissionRejected(
                f"Expected {session.count} dataset records, got {len(record_ids)}; "
                "reply with report_shortfall if fewer suitable datasets exist"
            )
        session.found = list(record_ids)
        return self._finish(session, "completed")

    @reply
    def report_shortfall(
        self,
        session: DatasetSession,
        record_ids: Annotated[
            list[str], Field(description="IDs of the dataset records found.")
        ],
        reason: Annotated[
            str, Field(description="Why no further suitable datasets were found.")
        ],
    ) -> Instruction:
        """Report that fewer suitable datasets exist than requested."""
        self._check_datasets(session, record_ids)
        if len(record_ids) >= session.count:
            raise SubmissionRejected(
                "This is not a shortfall; reply with complete instead"
            )
        session.found = list(record_ids)
        session.shortfall_reason = reason
        return self.instruction(
            session,
            self.section("shortfall"),
            context={
                "requested": session.count,
                "found": session.found,
                "reason": reason,
            },
            requires_user_response=True,
            replies=[self.continue_search, self.accept_partial],
        )

    @reply
    def continue_search(self, session: DatasetSession) -> Instruction:
        """Continue searching for the missing datasets."""
        return self._search(session)

    @reply
    def accept_partial(self, session: DatasetSession) -> Completion:
        """Accept the datasets found so far as a partial result."""
        return self._finish(session, "partial")

    def _finish(self, session: DatasetSession, outcome: str) -> Completion:
        return self.completion(
            session,
            {
                "outcome": outcome,
                "task": session.task,
                "requested": session.count,
                "datasets": session.found,
                "shortfall_reason": session.shortfall_reason,
                "verified": (
                    "record structure and recorded sources only; licensing and "
                    "suitability were not verified by Hiraku"
                ),
            },
        )

    def _search(self, session: DatasetSession) -> Instruction:
        return self.instruction(
            session,
            self.section("instructions"),
            context={
                "task": session.task,
                "count": session.count,
                "already_found": session.found,
            },
            replies=[self.complete, self.report_shortfall],
        )

    def _check_datasets(self, session: DatasetSession, record_ids: list[str]) -> None:
        if len(set(record_ids)) != len(record_ids):
            raise SubmissionRejected("Dataset record IDs must be distinct")
        for record_id in record_ids:
            record = load_record(record_id, config=self.config)
            if record.type != "dataset":
                raise SubmissionRejected(
                    f"Record {record_id!r} must have type 'dataset'"
                )
            if record.parent_id != session.task:
                raise SubmissionRejected(
                    f"Record {record_id!r} must be a child of task {session.task!r}"
                )
            sources = record.to_dict().get("sources")
            if not _has_sources(sources):
                raise SubmissionRejected(
                    f"Record {record_id!r} needs a non-empty 'sources' list of URLs "
                    "or references"
                )


def _has_sources(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and item.strip() for item in value)
    )


WORKFLOW = Autodataset
