"""Autodataset: discover and document candidate datasets in one instruction."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from hiraku.errors import SubmissionRejected
from hiraku.record import load_record
from hiraku.workflow import Instruction, Workflow, reply


class State(BaseModel):
    task: str
    count: int
    phase: Literal["search", "shortfall", "done"] = "search"
    found: list[str] = Field(default_factory=list)
    shortfall_reason: str | None = None
    outcome: Literal["completed", "partial"] | None = None


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
    ) -> State:
        """Start dataset discovery for a task record."""
        task = task or self.config.active_record_id
        if task is None:
            raise SubmissionRejected("No active record; pass the task record ID")
        record = load_record(task, config=self.config)
        return State(task=record.id, count=count)

    def next(self, state: State) -> Instruction:
        if state.phase == "done":
            return Instruction(
                self.markdown.section("## Done"),
                context={
                    "outcome": state.outcome,
                    "task": state.task,
                    "requested": state.count,
                    "datasets": state.found,
                    "shortfall_reason": state.shortfall_reason,
                    "verified": (
                        "record structure and recorded sources only; licensing and "
                        "suitability were not verified by Hiraku"
                    ),
                },
                final=True,
            )
        if state.phase == "shortfall":
            return Instruction(
                self.markdown.section("## Shortfall"),
                context={
                    "requested": state.count,
                    "found": state.found,
                    "reason": state.shortfall_reason,
                },
                replies=[self.continue_search, self.accept_partial],
                requires_user_response=True,
            )
        return Instruction(
            self.markdown.section("## Instructions"),
            context={
                "task": state.task,
                "count": state.count,
                "already_found": state.found,
            },
            replies=[self.complete, self.report_shortfall],
        )

    @reply
    def complete(
        self,
        state: State,
        record_ids: Annotated[
            list[str], Field(description="IDs of all dataset records found.")
        ],
    ) -> None:
        """Submit the requested number of dataset records."""
        self._check_datasets(state, record_ids)
        if len(record_ids) != state.count:
            raise SubmissionRejected(
                f"Expected {state.count} dataset records, got {len(record_ids)}; "
                "reply with report_shortfall if fewer suitable datasets exist"
            )
        state.found = list(record_ids)
        state.phase, state.outcome = "done", "completed"

    @reply
    def report_shortfall(
        self,
        state: State,
        record_ids: Annotated[
            list[str], Field(description="IDs of the dataset records found.")
        ],
        reason: Annotated[
            str, Field(description="Why no further suitable datasets were found.")
        ],
    ) -> None:
        """Report that fewer suitable datasets exist than requested."""
        self._check_datasets(state, record_ids)
        if len(record_ids) >= state.count:
            raise SubmissionRejected(
                "This is not a shortfall; reply with complete instead"
            )
        state.found = list(record_ids)
        state.shortfall_reason = reason
        state.phase = "shortfall"

    @reply
    def continue_search(self, state: State) -> None:
        """Continue searching for the missing datasets."""
        state.phase = "search"

    @reply
    def accept_partial(self, state: State) -> None:
        """Accept the datasets found so far as a partial result."""
        state.phase, state.outcome = "done", "partial"

    def _check_datasets(self, state: State, record_ids: list[str]) -> None:
        if len(set(record_ids)) != len(record_ids):
            raise SubmissionRejected("Dataset record IDs must be distinct")
        for record_id in record_ids:
            record = load_record(record_id, config=self.config)
            if record.type != "dataset":
                raise SubmissionRejected(
                    f"Record {record_id!r} must have type 'dataset'"
                )
            if record.parent_id != state.task:
                raise SubmissionRejected(
                    f"Record {record_id!r} must be a child of task {state.task!r}"
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
