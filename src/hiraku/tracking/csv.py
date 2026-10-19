"""CSV tracking backend."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict

from ..config import HirakuConfig
from ..errors import BackendError, ValidationError
from .base import TrackingBackend

if TYPE_CHECKING:
    from ..run import Run


class CSVBackendConfig(BaseModel):
    """Validated options for the CSV tracking backend.

    Attributes:
        type: Backend discriminator; must be "csv".
        path: Optional CSV path override.
    """

    model_config = ConfigDict(extra="forbid")
    type: Literal["csv"] = "csv"
    path: Path | None = None


class CSVBackend(TrackingBackend):
    """Normalize runs from a project CSV file.

    Args:
        config: Project configuration used to resolve the default CSV path.
        path: Optional explicit CSV path.
    """

    STANDARD = {
        "id",
        "record_id",
        "job_id",
        "commit",
        "timestamp",
        "duration",
        "entrypoint",
        "url",
    }

    def __init__(self, config: HirakuConfig, path: Path | str | None = None) -> None:
        """Create a CSV adapter.

        Args:
            config: Project configuration.
            path: Optional CSV path override.
        """
        self.config = config
        if path is None:
            self.path = None
        else:
            candidate = Path(path).expanduser()
            self.path = (
                candidate.resolve()
                if candidate.is_absolute()
                else (config.root / candidate).resolve()
            )

    def _read_source_runs(self, **options: Any) -> list[Run]:
        from ..run import Run

        path = self.path or self.config.root / "runs.csv"
        if not path.exists():
            return []
        runs: list[Run] = []
        try:
            with path.open(newline="", encoding="utf-8") as stream:
                reader = csv.DictReader(stream)
                if reader.fieldnames is None or "id" not in reader.fieldnames:
                    raise BackendError(f"csv backend file {path} has no id column")
                if "record_id" not in reader.fieldnames:
                    raise BackendError(
                        f"csv backend file {path} has no record_id column"
                    )
                if any(not name for name in reader.fieldnames) or len(
                    set(reader.fieldnames)
                ) != len(reader.fieldnames):
                    raise BackendError(
                        f"csv backend file {path} has invalid or duplicate columns"
                    )
                for row_number, row in enumerate(reader, start=2):
                    try:
                        if None in row:
                            raise ValueError("row has more values than columns")
                        run_id = row.get("id", "")
                        if not run_id:
                            raise ValueError("id is empty")
                        metrics = {
                            key.removeprefix("metric."): value
                            for key, value in row.items()
                            if key.startswith("metric.") and value != ""
                        }
                        params = {
                            key.removeprefix("param."): value
                            for key, value in row.items()
                            if key.startswith("param.") and value != ""
                        }
                        raw = {
                            key: value
                            for key, value in row.items()
                            if key not in self.STANDARD
                            and not key.startswith("metric.")
                            and not key.startswith("param.")
                        }
                        normalized = {
                            key: (value if value != "" else None)
                            for key, value in row.items()
                        }
                        run = Run(
                            id=run_id,
                            record_id=normalized.get("record_id"),
                            job_id=normalized.get("job_id"),
                            commit=normalized.get("commit"),
                            timestamp=self._timestamp(
                                normalized.get("timestamp"),
                                backend="csv",
                                context=f"{path} row {row_number}",
                            ),
                            duration=normalized.get("duration"),
                            entrypoint=normalized.get("entrypoint"),
                            url=normalized.get("url"),
                            metrics=metrics,
                            params=params,
                            raw=raw,
                        )
                    except (
                        ValueError,
                        TypeError,
                        ValidationError,
                        BackendError,
                    ) as exc:
                        if isinstance(exc, BackendError):
                            raise
                        raise BackendError(
                            f"csv backend malformed row in {path} "
                            f"at row {row_number}: {exc}"
                        ) from exc
                    runs.append(run)
        except BackendError:
            raise
        except (OSError, csv.Error, UnicodeError) as exc:
            raise BackendError(f"csv backend could not read {path}: {exc}") from exc
        return runs
