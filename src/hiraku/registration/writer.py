"""Atomic, field-scoped persistence for reviewed import plans.

The writer executes a plan produced by :mod:`hiraku.registration.review`. Each
record is saved atomically through Hiraku's own persistence; nothing is written
to the project root except the records themselves, and a registration never
writes a run or a tracking backend. Writes are per record: a failure later in
the batch leaves earlier records in place, and the stable IDs make a retry safe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config import HirakuConfig
from ..record import Record
from .review import _Write


@dataclass
class ApplyResult:
    """An auditable summary of what the writer did or failed to do."""

    applied: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


def apply_writes(config: HirakuConfig, writes: list[_Write]) -> ApplyResult:
    """Apply an ordered list of writes, creating and updating records.

    ``writes`` are ordered parents before children by the planner. Creates use
    ``Record.create`` (no activation); updates preserve human Markdown and any
    field the importer does not own, then save through the public record API.
    A failed write is recorded and the remaining writes are still attempted, so
    the audit summary is complete and a retry is idempotent.
    """
    result = ApplyResult()
    for write in writes:
        try:
            if write.create:
                _create(write, config)
            else:
                _update(write, config)
        except Exception as exc:  # noqa: BLE001 - audit every I/O failure
            result.failed.append((write.record_id, str(exc)))
            continue
        result.applied.append(write.record_id)
    return result


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
