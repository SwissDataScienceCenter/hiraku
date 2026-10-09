"""View-only dashboard support for Hiraku.

The dashboard reads research records and tracking runs without mutating
project files. Importing this package must not require the optional
``dashboard`` dependencies; only the packaged marimo application and launcher
import marimo.
"""

from .data import (
    SNAPSHOT_VERSION,
    DashboardError,
    DashboardSnapshot,
    RecordView,
    RunView,
    SourceInfo,
    build_snapshot,
    read_record_index,
    sort_runs,
)

__all__ = [
    "SNAPSHOT_VERSION",
    "DashboardError",
    "DashboardSnapshot",
    "RecordView",
    "RunView",
    "SourceInfo",
    "build_snapshot",
    "read_record_index",
    "sort_runs",
]
