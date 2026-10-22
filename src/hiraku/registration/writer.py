"""Atomic, field-scoped persistence for reviewed import plans.

The writer executes a plan produced by :mod:`hiraku.registration.review`. Each
record is saved atomically through Hiraku's own persistence; nothing is written
to the project root except the records themselves, and a registration never
writes a run or a tracking backend.
"""

from __future__ import annotations

from typing import Any

from ..config import HirakuConfig
from ..record import Record
from .review import _Write


def apply_writes(config: HirakuConfig, writes: list[_Write]) -> None:
    """Apply an ordered list of writes, creating and updating records.

    ``writes`` are ordered parents before children by the planner. Creates use
    ``Record.create`` (no activation); updates preserve human Markdown and any
    field the importer does not own, then save through the public record API.
    """
    for write in writes:
        if write.create:
            _create(write, config)
        else:
            _update(write, config)


def _create(write: _Write, config: HirakuConfig) -> None:
    fields: dict[str, Any] = {
        "type": write.record_type,
        "registration": write.registration.to_record_value(),
    }
    if write.parent_id is not None:
        fields["parent_id"] = write.parent_id
    Record.create(write.record_id, config=config, activate=False, **fields)


def _update(write: _Write, config: HirakuConfig) -> None:
    record = write.existing
    if record is None or record.path is None:
        # The record disappeared since planning; re-create it as a new dataset.
        _create(write, config)
        return
    record.type = write.record_type
    record.registration = write.registration.to_record_value()
    if write.parent_id is not None:
        record.parent_id = write.parent_id
    record.save()
