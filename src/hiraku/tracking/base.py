"""Shared tracking backend and query normalization."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ..errors import BackendError

if TYPE_CHECKING:
    from ..run import Run


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
        runs: list[Run],
        *,
        sort: str | None,
        ascending: bool | None,
        limit: int | None,
    ) -> list[Run]:
        result = list(runs)
        if sort is not None:

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

            def numeric_key(run: Run) -> float:
                number = numeric(run)
                assert number is not None
                return number

            ranked.sort(
                key=numeric_key,
                reverse=ascending is False,
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
        if limit is not None:
            result = result[:limit]
        return result

    def _validate_query(
        self,
        *,
        sort: str | None,
        ascending: bool | None,
        limit: int | None,
    ) -> None:
        if sort is not None and (not isinstance(sort, str) or not sort):
            raise BackendError(
                f"{self.backend_name} backend sort must be a non-empty string"
            )
        if ascending is not None and not isinstance(ascending, bool):
            raise BackendError(
                f"{self.backend_name} backend ascending must be a boolean"
            )
        if sort is None and ascending is not None:
            raise BackendError(
                f"{self.backend_name} backend ascending requires a sort field"
            )
        if limit is not None and (
            not isinstance(limit, int) or isinstance(limit, bool) or limit < 0
        ):
            raise BackendError(
                f"{self.backend_name} backend limit must be a non-negative integer"
            )

    def list_runs(
        self,
        *,
        record_id: str | None = None,
        sort: str | None = None,
        ascending: bool | None = None,
        limit: int | None = None,
    ) -> list[Run]:
        """Return normalized runs with record attribution.

        Args:
            record_id: Optional record ID to filter runs by; all attributed runs
                are returned when omitted.
            sort: Run field, metric, or parameter. Numeric values sort first;
                runs without a numeric value follow, ordered by ID.
            ascending: Sort order; requires ``sort``. Defaults to ascending.
            limit: Maximum number of returned runs. Without ``sort``, runs are
                ordered by timestamp, with missing timestamps last.

        Returns:
            A list of normalized Run objects matching the specified filters and query.
        """
        if record_id is not None:
            self._validate_filters(record_id)
        self._validate_query(sort=sort, ascending=ascending, limit=limit)
        runs = [run for run in self._read_source_runs() if run.record_id]
        if record_id is not None:
            runs = [run for run in runs if run.record_id == record_id]
        return self._apply_query(
            runs,
            sort=sort,
            ascending=ascending,
            limit=limit,
        )

    @abstractmethod
    def _read_source_runs(self) -> list[Run]:
        """Read all runs from the configured source once."""
        raise NotImplementedError

    @property
    def backend_name(self) -> str:
        return type(self).__name__.removesuffix("Backend").lower()
