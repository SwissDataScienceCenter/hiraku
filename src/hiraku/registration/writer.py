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

from ..base import BaseRecord
from ..config import HirakuConfig
from ..errors import RecordExistsError, RecordNotFoundError
from ..record import Record, _record_index, _root_id, _validate_tree
from .review import _Write


@dataclass
class ApplyResult:
    """An auditable summary of what the writer did or failed to do."""

    applied: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


def apply_writes(config: HirakuConfig, writes: list[_Write]) -> ApplyResult:
    """Apply an ordered list of writes, creating and updating records.

    ``writes`` are ordered parents before children by the planner. The initial
    record tree is validated once, then new records extend that checked index
    and use the same atomic persistence as ``Record.create``. Updates preserve
    human Markdown and any field the importer does not own.
    A failed write is recorded and the remaining writes are still attempted, so
    the audit summary is complete and a retry is idempotent.
    """
    result = ApplyResult()
    index = _record_index(config)
    _validate_tree(index, config.records_dir)
    for write in writes:
        try:
            if write.create:
                _create(write, config, index)
            else:
                if _update(write, config, index):
                    # Reparenting can relocate a subtree.
                    index = _record_index(config)
        except Exception as exc:  # noqa: BLE001 - audit every I/O failure
            result.failed.append((write.record_id, str(exc)))
            continue
        result.applied.append(write.record_id)
    return result


def _create(write: _Write, config: HirakuConfig, index: dict[str, Record]) -> None:
    if write.record_id in index:
        raise RecordExistsError(f"Record {write.record_id!r} already exists")
    if write.parent_id is not None and write.parent_id not in index:
        raise RecordNotFoundError(f"Parent record {write.parent_id!r} not found")
    fields: dict[str, Any] = {
        "type": write.record_type,
        "registration": write.registration.to_record_value(),
    }
    record = Record(id=write.record_id, parent_id=write.parent_id, **fields)
    root_id = (
        _root_id(write.parent_id, index)
        if write.parent_id is not None
        else write.record_id
    )
    target = config.records_dir / root_id / f"{write.record_id}.md"
    BaseRecord.save(record, target)
    index[record.id] = record


def _update(write: _Write, config: HirakuConfig, index: dict[str, Record]) -> bool:
    record = write.existing
    if record is None or record.path is None:
        # The record disappeared since planning; re-create it as a new dataset.
        index.pop(write.record_id, None)
        _create(write, config, index)
        return False
    record.type = write.record_type
    record.registration = write.registration.to_record_value()
    if write.parent_id is not None and record.parent_id != write.parent_id:
        # Hiraku requires a record to live under its parent's root folder.
        # ``reparent`` relocates the file and persists the fields set above.
        record.reparent(write.parent_id, config=config)
        return True
    if write.parent_id is not None:
        record.parent_id = write.parent_id
    BaseRecord.save(record)
    index[record.id] = record
    return False
