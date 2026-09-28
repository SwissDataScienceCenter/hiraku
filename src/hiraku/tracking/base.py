"""Shared tracking backend and query normalization."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ..errors import BackendError

if TYPE_CHECKING:
    from ..data import Run


class TrackingBackend(ABC):
    """Abstract tracking backend implemented by every run-tracking adapter."""

    @staticmethod
    def _validate_filters(record_id: str) -> None:
        if not isinstance(record_id, str) or not record_id:
            raise BackendError("Tracking queries require a non-empty record_id")

    @staticmethod
    def _timestamp(value: Any, *, backend: str, context: str) -> datetime | None:
        if value is None or value == "":
            return None
        try:
            if isinstance(value, datetime):
                parsed = value
            elif isinstance(value, int | float):
                parsed = datetime.fromtimestamp(value / 1000, tz=UTC)
            elif isinstance(value, str):
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            else:
                raise TypeError(type(value).__name__)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except (ValueError, TypeError, OSError) as exc:
            raise BackendError(
                f"{backend} backend returned invalid timestamp for {context}: {value!r}"
            ) from exc

    @staticmethod
    def _apply_query(
        runs: list[Run], query: Mapping[str, Any], backend: str
    ) -> list[Run]:
        allowed = {"sort", "ascending", "limit"}
        unknown = set(query) - allowed
        if unknown:
            raise BackendError(
                f"{backend} backend does not support query options: {sorted(unknown)}"
            )
        result = list(runs)
        sort = query.get("sort")
        if sort is None and "ascending" in query:
            raise BackendError(f"{backend} backend ascending requires a sort field")
        if sort is not None:
            if not isinstance(sort, str):
                raise BackendError(f"{backend} backend sort must be a string")

            def value(run: Run) -> Any:
                value = getattr(run, sort, None)
                if value is None:
                    value = run.metrics.get(sort, run.params.get(sort))
                return value

            def numeric(run: Run) -> float | None:
                candidate = value(run)
                if isinstance(candidate, bool) or candidate is None:
                    return None
                try:
                    return float(candidate)
                except (TypeError, ValueError):
                    return None

            ranked = [run for run in result if numeric(run) is not None]
            unranked = [run for run in result if numeric(run) is None]
            ranked.sort(key=lambda run: run.id)
            ranked.sort(
                key=numeric,
                reverse=not bool(query.get("ascending", True)),
            )
            result = [*ranked, *sorted(unranked, key=lambda run: run.id)]
        else:
            result.sort(
                key=lambda run: (
                    run.timestamp is None,
                    run.timestamp or datetime.max.replace(tzinfo=UTC),
                    run.id,
                )
            )
        limit = query.get("limit")
        if limit is not None:
            if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
                raise BackendError(
                    f"{backend} backend limit must be a non-negative integer"
                )
            result = result[:limit]
        return result

    def list_runs(self, *, record_id: str, **query: Any) -> list[Run]:
        """Return normalized runs for a task and optional experiment.

        Args:
            record_id: The record ID to filter runs by.
            query: Optional query parameters to sort, limit, or filter the results.

        Returns:
            A list of normalized Run objects matching the specified filters and query.
        """

        self._validate_filters(record_id)
        runs = self._read_runs(record_id=record_id)
        runs = [run for run in runs if run.record_id == record_id]
        return self._apply_query(runs, query, self.backend_name)

    @property
    def backend_name(self) -> str:
        return type(self).__name__.removesuffix("Backend").lower()

    @abstractmethod
    def _read_runs(self, *, record_id: str) -> list[Run]:
        raise NotImplementedError
