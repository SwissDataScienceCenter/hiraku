"""Refresh session for the dashboard.

The session owns the last successful snapshot and coalesces refresh requests
so a slow tracking backend cannot accumulate concurrent queries. It is plain
Python and testable without marimo.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from ..config import HirakuConfig
from ..record import Record
from .data import (
    DashboardError,
    DashboardSnapshot,
    RunView,
    build_snapshot,
    read_record_index,
)


@dataclass(frozen=True)
class RefreshResult:
    """Outcome of one dashboard refresh request."""

    snapshot: DashboardSnapshot | None
    error: str | None
    coalesced: bool


class DashboardSession:
    """Coalesce dashboard refreshes and retain the last successful snapshot."""

    def __init__(self, config: HirakuConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._in_flight = False
        self._last: DashboardSnapshot | None = None
        self._records: tuple[dict[str, Record], list[DashboardError]] | None = None
        self._sources: dict[str, tuple[RunView, ...]] = {}
        self.selected_run: str | None = None
        self._refreshed_at: datetime | None = None

    def reload_records(self) -> list[str]:
        """Start a new refresh generation and return current record IDs."""
        self._records = read_record_index(self._config)
        self._sources.clear()
        self._refreshed_at = datetime.now(UTC)
        return sorted(self._records[0])

    def select_run(self, rows: Any) -> None:
        if len(rows):
            self.selected_run = rows.iloc[0]["key"]

    def refresh(
        self,
        record_id: str | None,
        scope: str = "record",
        *,
        use_active_record: bool = True,
        cached: bool = False,
    ) -> RefreshResult:
        """Refresh once, or return the last snapshot if one is in flight."""
        selected = record_id or (
            self._config.active_record_id if use_active_record else None
        )
        with self._lock:
            if self._in_flight:
                if (
                    self._last is not None
                    and self._last.selected_record_id == selected
                    and self._last.scope == scope
                ):
                    return RefreshResult(
                        replace(self._last, stale=True),
                        "Refresh skipped: another refresh is already in flight.",
                        True,
                    )
                return RefreshResult(
                    None,
                    "Refresh skipped: another refresh is already in flight.",
                    True,
                )
            self._in_flight = True
        try:
            snapshot = build_snapshot(
                self._config,
                record_id=record_id,
                scope=scope,
                use_active_record=use_active_record,
                _record_data=self._records if cached else None,
                _source_cache=self._sources if cached else None,
                refresh_time=self._refreshed_at if cached else None,
            )
            failures = [
                error.message
                for error in snapshot.errors
                if error.kind in {"backend", "transient"}
            ]
            if failures:
                with self._lock:
                    last = self._last
                if (
                    last is not None
                    and last.selected_record_id == snapshot.selected_record_id
                    and last.scope == snapshot.scope
                ):
                    return RefreshResult(
                        replace(last, stale=True, errors=snapshot.errors),
                        " ".join(failures),
                        False,
                    )
                return RefreshResult(snapshot, " ".join(failures), False)
            with self._lock:
                self._last = snapshot
            return RefreshResult(snapshot, None, False)
        except Exception as exc:
            with self._lock:
                last = self._last
            if (
                last is not None
                and last.selected_record_id == selected
                and last.scope == scope
            ):
                return RefreshResult(
                    replace(last, stale=True),
                    str(exc),
                    False,
                )
            return RefreshResult(None, str(exc), False)
        finally:
            with self._lock:
                self._in_flight = False
