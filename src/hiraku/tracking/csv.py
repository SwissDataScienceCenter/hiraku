"""CSV tracking backend."""

from __future__ import annotations

import csv
import os
import re
from pathlib import Path

from ..errors import BackendError, ValidationError
from ..project import Project
from .base import BackendOperations, CSVBackendConfig, Run


class CSVBackend(BackendOperations):
    """Normalize runs from a project CSV file.

    Args:
        project: Hiraku project used to resolve relative CSV paths.
        path: Explicit CSV path; required unless settings are supplied.
    """

    STANDARD = {
        "id",
        "hiraku_job_id",
        "record_id",
        "commit",
        "timestamp",
        "duration",
        "url",
    }

    def __init__(
        self,
        project: Project,
        path: Path | str | None = None,
        *,
        settings: CSVBackendConfig | None = None,
    ) -> None:
        """Create a CSV adapter.

        Args:
            project: Hiraku project.
            path: CSV path, supporting ``${NAME}`` environment references.
            settings: Pre-validated CSV backend settings supplied by a leaderboard.
        """
        if settings is not None and path is not None:
            raise BackendError("Pass CSV backend settings or path, not both")
        if settings is None:
            if path is None:
                raise BackendError("CSV backend requires a path")
            settings = CSVBackendConfig(path=path)
        if not isinstance(settings, CSVBackendConfig):
            raise BackendError("CSV backend settings must be CSVBackendConfig")
        self.project = project

        def expand(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in os.environ:
                raise BackendError(f"CSV path environment variable {name!r} is not set")
            return os.environ[name]

        expanded = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", expand, str(settings.path))
        if not expanded.strip():
            raise BackendError("CSV path must not be blank after variable expansion")
        candidate = Path(expanded).expanduser()
        self.path = (project.root / candidate).resolve()

    def _read_source_runs(self) -> list[Run]:
        path = self.path
        if not path.exists():
            return []
        runs: list[Run] = []
        try:
            with path.open(newline="", encoding="utf-8") as stream:
                reader = csv.DictReader(stream)
                if reader.fieldnames is None or "id" not in reader.fieldnames:
                    raise BackendError(f"csv backend file {path} has no id column")
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
                        record_reference = normalized.get("record_id")
                        run = Run(
                            id=run_id,
                            job_id=normalized.get("hiraku_job_id"),
                            record_id=record_reference,
                            commit=normalized.get("commit"),
                            timestamp=self._timestamp(
                                normalized.get("timestamp"),
                                backend="csv",
                                context=f"{path} row {row_number}",
                            ),
                            duration=normalized.get("duration"),
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
