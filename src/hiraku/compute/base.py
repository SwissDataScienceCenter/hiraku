"""Common compute lifecycle interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from ..job import JobSpec


class ComputeBackend(ABC):
    """Submit executions and inspect/control them independently of tracking."""

    @abstractmethod
    def submit(self, spec: JobSpec) -> dict[str, object]:
        """Start an execution and return an opaque backend handle."""

    @abstractmethod
    def status(self, handle: dict[str, object]) -> dict[str, object]:
        """Observe backend state without assuming that an unreachable job failed."""

    @abstractmethod
    def cancel(self, handle: dict[str, object]) -> None:
        """Request cancellation of an execution."""

    @abstractmethod
    def logs(
        self, handle: dict[str, object], *, stream: str, follow: bool
    ) -> Iterator[str]:
        """Read execution logs."""
