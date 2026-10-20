"""Scheduler: work through records by priority until each reaches a target."""

from __future__ import annotations

import random
import secrets
from typing import Annotated, Any

from pydantic import Field

from hiraku.errors import EventRejected
from hiraku.markdown import render
from hiraku.record import Record, load_record
from hiraku.workflows import Instruction, State, Workflow, event, node, start


class Queue(State):
    node: str = "working"
    parent: str
    target: int
    current: str | None = None
    baseline: int = 0


class Scheduler(Workflow):
    """Pick the open record with the highest priority and let the agent work on it.

    Start creates experiment records with a random priority and a count of 0.
    Each instruction assigns the open record with the highest priority, and the
    agent raises its count by one, until every count reaches the target. The
    next step comes from the records, not from a fixed sequence.
    """

    @start(dest="working")
    def start(
        self,
        *,
        records: Annotated[int, "Number of records to create.", Field(gt=0, le=20)] = 3,
        target: Annotated[int, "Count each record must reach.", Field(gt=0, le=10)] = 2,
    ) -> Queue:
        """Create records with random priorities and start working through them."""
        parent = Record.create(
            f"scheduler-{secrets.token_hex(3)}",
            type="task",
            summary=f"Scheduler example: raise each count to {target}",
            activate=False,
            config=self.config,
        )
        for index in range(records):
            Record.create(
                f"{parent.id}-{index + 1}",
                parent_id=parent.id,
                type="experiment",
                priority=random.randint(1, 9),
                count=0,
                activate=False,
                config=self.config,
            )
        queue = Queue(parent=parent.id, target=target)
        return self._assign(queue)

    @node("working")
    def work(self, queue: Queue) -> Instruction:
        record = load_record(str(queue.current), config=self.config)
        values = {
            "record": record.id,
            "count": queue.baseline,
            "next": queue.baseline + 1,
        }
        return Instruction(
            render(self.markdown.section("## Work"), values),
            context={
                **values,
                "priority": _priority(record),
                "remaining": sum(
                    queue.target - _count(item)
                    for item in self._children(queue)
                    if _count(item) < queue.target
                ),
            },
        )

    @node("finished", final=True)
    def report(self, queue: Queue) -> Instruction:
        return Instruction(
            self.markdown.section("## Done"), context={"records": self._summary(queue)}
        )

    @event(source="working", dest=["working", "finished"])
    def incremented(
        self,
        queue: Queue,
        *,
        record_id: Annotated[str, "ID of the assigned record."],
    ) -> Queue:
        """Report that the assigned record's count is one higher."""
        if record_id != queue.current:
            raise EventRejected(
                f"Work on the assigned record {queue.current!r}, not {record_id!r}"
            )
        count = _count(load_record(record_id, config=self.config))
        if count != queue.baseline + 1:
            raise EventRejected(
                f"The count of {record_id!r} is {count}; set it to {queue.baseline + 1}"
            )
        return self._assign(queue)

    def _assign(self, queue: Queue) -> Queue:
        """Assign the highest-priority open record, or finish when none remain."""
        open_records = [
            item for item in self._children(queue) if _count(item) < queue.target
        ]
        pick = max(
            open_records, key=lambda item: (_priority(item), item.id), default=None
        )
        queue.node = "working" if pick is not None else "finished"
        queue.current = pick.id if pick is not None else None
        queue.baseline = _count(pick) if pick is not None else 0
        return queue

    def _children(self, queue: Queue) -> list[Record]:
        parent = load_record(queue.parent, config=self.config)
        return sorted(parent.children(config=self.config), key=lambda item: item.id)

    def _summary(self, queue: Queue) -> list[dict[str, Any]]:
        return [
            {"record": item.id, "priority": _priority(item), "count": _count(item)}
            for item in self._children(queue)
        ]


def _priority(record: Record) -> int:
    return int(getattr(record, "priority", 0))


def _count(record: Record) -> int:
    value = getattr(record, "count", 0)
    if isinstance(value, bool) or not isinstance(value, int):
        raise EventRejected(
            f"The count of {record.id!r} must be an integer, not {value!r}"
        )
    return value


WORKFLOW = Scheduler
