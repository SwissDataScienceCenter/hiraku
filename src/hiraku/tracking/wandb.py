"""W&B tracking backend."""

from __future__ import annotations

from typing import Any, Literal

from ..errors import BackendError
from .base import BackendOperations, Run, WandbBackendConfig


class WandbBackend(BackendOperations):
    """Normalize native W&B runs without importing W&B eagerly.

    Args:
        project: W&B project name.
        settings: Pre-validated W&B settings supplied by a leaderboard.
        entity: Optional W&B entity.
        id: Optional W&B run ID.
        experiment_name: Optional W&B experiment name.
        notes: Optional W&B run notes.
        tags: Optional W&B run tags.
        group: Optional W&B run group.
        job_type: Optional W&B run job type.
        mode: W&B run mode; defaults to "online".
        api_key: Optional W&B API key.
    """

    def __init__(
        self,
        project: str,
        *,
        settings: WandbBackendConfig | None = None,
        entity: str | None = None,
        id: str | None = None,
        experiment_name: str | None = None,
        notes: str | None = None,
        tags: list[str] | None = None,
        group: str | None = None,
        job_type: str | None = None,
        mode: Literal["online", "offline"] = "online",
        api_key: str | None = None,
    ) -> None:
        """Create an W&B adapter from validated settings.

        Args:
            settings: Pre-validated W&B settings supplied by a leaderboard.
            entity: Optional W&B entity.
            project: Explicit W&B project name.
            id: Optional W&B run ID.
            experiment_name: Optional W&B experiment name.
            notes: Optional W&B run notes.
            tags: Optional W&B run tags.
            group: Optional W&B run group.
            job_type: Optional W&B run job type.
            mode: W&B run mode; defaults to "online".
            api_key: Optional W&B API key.

        Raises:
            hiraku.errors.BackendError: If any settings has conflicts.
        """
        if settings is None:
            settings = WandbBackendConfig(
                entity=entity,
                project=project,
                id=id,
                experiment_name=experiment_name,
                notes=notes,
                tags=tags,
                group=group,
                job_type=job_type,
                mode=mode,
                api_key=api_key,
            )
        elif any(
            [
                entity is not None,
                id is not None,
                experiment_name is not None,
                notes is not None,
                tags is not None,
                group is not None,
                job_type is not None,
                mode != "online",
                api_key is not None,
            ]
        ):
            raise BackendError("Pass W&B backend settings or options, not both")
        if not isinstance(settings, WandbBackendConfig):
            raise BackendError("W&B backend settings must be WandbBackendConfig")
        if project != settings.project:
            raise BackendError("W&B project must match backend settings")
        self.entity = settings.entity
        self.project = settings.project
        if not self.project:
            raise BackendError("W&B project must be explicitly configured")
        self.id = settings.id
        self.experiment_name = settings.experiment_name
        self.notes = settings.notes
        self.tags = settings.tags
        self.group = settings.group
        self.job_type = settings.job_type
        self.mode = settings.mode
        self.api_key = settings.api_key

        # Verify that the W&B library is available and import it.
        self._verify_wandb_import()

        # Initialize the W&B API client.
        self._api = self._get_api()

    def _verify_wandb_import(self) -> None:
        try:
            import wandb  # noqa: F401
        except ImportError as exc:
            raise BackendError(
                "wandb backend requires the optional 'wandb' dependency"
            ) from exc

    def _get_api(self) -> Any:
        import wandb

        return wandb.Api(api_key=self.api_key)

    def _get_runs(self) -> Any:
        path = f"{self.entity}/{self.project}" if self.entity else self.project
        return self._api.runs(path)

    @staticmethod
    def _run_config(run: Any) -> dict[str, Any]:
        """Return a plain configuration mapping for a W&B run."""
        return dict(run.config or {})

    @classmethod
    def _matches(
        cls,
        run: Any,
        *,
        record_id: str,
    ) -> bool:
        """Whether a W&B run contains matching Hiraku provenance."""
        config = cls._run_config(run)
        return config.get("hiraku.record_id") == record_id

    @classmethod
    def _run_params(cls, run: Any) -> dict[str, Any]:
        """Extract user parameters while excluding Hiraku provenance keys."""
        return {
            key: value
            for key, value in cls._run_config(run).items()
            if not key.startswith("hiraku.")
        }

    @staticmethod
    def _run_raw(run: Any) -> dict[str, Any]:
        """Return backend-specific public metadata for a W&B run."""
        return {
            "state": run.state,
            "entity": run.entity,
            "project": run.project,
            "tags": list(run.tags or []),
            "notes": run.notes,
            "group": getattr(run, "group", None),
            "job_type": getattr(run, "job_type", None),
            "metadata": run.metadata,
        }

    def _normalize_run(self, run: Any) -> Run:
        """Convert one W&B public run object into Hiraku's normalized model."""
        config = self._run_config(run)
        record_id = config.get("hiraku.record_id")
        commit = config.get("hiraku.commit")
        raw = self._run_raw(run)
        summary = dict(run.summary or {})
        metrics = {
            key: value
            for key, value in summary.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        return Run(
            id=run.id,
            job_id=config.get("hiraku.job_id"),
            metrics=metrics,
            params={key: str(value) for key, value in self._run_params(run).items()},
            record_id=record_id,
            commit=commit,
            timestamp=run.created_at,
            # W&B keeps a run's wall-clock seconds in the ``_runtime`` summary key.
            duration=summary.get("_runtime"),
            url=run.url,
            raw=raw,
        )

    def _read_source_runs(self) -> list[Run]:
        try:
            runs = self._get_runs()
            return [self._normalize_run(run) for run in runs]
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(f"wandb backend query failed: {exc}") from exc
