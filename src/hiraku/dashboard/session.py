"""Refresh session for the dashboard.

The session owns the last successful snapshot and coalesces refresh requests
so a slow tracking backend cannot accumulate concurrent queries. It is plain
Python and testable without marimo.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace

from ..config import HirakuConfig
from .data import DashboardSnapshot, build_snapshot


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

    def refresh(
        self,
        record_id: str | None,
        scope: str = "record",
        *,
        use_active_record: bool = True,
    ) -> RefreshResult:
        """Refresh once, or return the last snapshot if one is in flight."""
        with self._lock:
            if self._in_flight:
                if self._last is not None:
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
            )
            with self._lock:
                self._last = snapshot
            return RefreshResult(snapshot, None, False)
        except Exception as exc:
            with self._lock:
                last = self._last
            if last is not None:
                return RefreshResult(
                    replace(last, stale=True),
                    str(exc),
                    False,
                )
            return RefreshResult(None, str(exc), False)
        finally:
            with self._lock:
                self._in_flight = False
