"""CSV tracking backend."""

from __future__ import annotations

import csv
from pathlib import Path

from ..errors import BackendError, ValidationError
from ..project import Project
from .base import BackendOperations, CSVBackendConfig, Run


class CSVBackend(BackendOperations):
    """Normalize runs from a project CSV file.

    Args:
        project: Hiraku project used to resolve the default CSV path.
        path: Optional explicit CSV path.
    """

    STANDARD = {
        "id",
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
            path: Optional CSV path override.
            settings: Pre-validated CSV backend settings used by the factory.
        """
        if settings is not None and path is not None:
            raise BackendError("Pass CSV backend settings or path, not both")
        if settings is None:
            settings = CSVBackendConfig(path=path)
        if not isinstance(settings, CSVBackendConfig):
            raise BackendError("CSV backend settings must be CSVBackendConfig")
        self.project = project
        if settings.path is None:
            self.path = None
        else:
            candidate = settings.path.expanduser()
            self.path = (
                candidate.resolve()
                if candidate.is_absolute()
                else (project.root / candidate).resolve()
            )

    def _read_source_runs(self) -> list[Run]:
        path = self.path or self.project.root / "runs.csv"
        if not path.exists():
            return []
        runs: list[Run] = []
        records = self.project.vault.list()
        record_references = {record.id: record.id for record in records}
        names: dict[str, list[str]] = {}
        for record in records:
            names.setdefault(record.name, []).append(record.id)
        record_references.update(
            {name: ids[0] for name, ids in names.items() if len(ids) == 1}
        )
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
                        record_reference = normalized.get("record_id")
                        run = Run(
                            id=run_id,
                            record_id=record_references.get(
                                record_reference, record_reference
                            ),
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
