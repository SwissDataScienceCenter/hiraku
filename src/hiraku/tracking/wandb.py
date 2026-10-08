"""W&B tracking backend."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..core.config import HirakuConfig
from ..core.errors import BackendError
from .base import TrackingBackend

if TYPE_CHECKING:
    from ..core.run import Run


class WandbBackendConfig(BaseModel):
    """Validated options for the W&B tracking backend.

    Attributes:
        type: Backend discriminator; must be "wandb".
        entity: Optional W&B entity.
        project: Required W&B project.
        id: Optional W&B run ID.
        experiment_name: Optional W&B experiment name.
        notes: Optional W&B run notes.
        tags: Optional W&B run tags.
        group: Optional W&B run group.
        job_type: Optional W&B run job type.
        mode: W&B run mode; defaults to "online".
        api_key: Optional W&B API key. Defaults to the WANDB_API_KEY env variable.
    """

    model_config = ConfigDict(extra="forbid")
    type: Literal["wandb"] = "wandb"
    entity: str | None = None
    project: str = Field(..., min_length=1)
    id: str | None = None
    experiment_name: str | None = Field(default=None, min_length=1)
    notes: str | None = None
    tags: list[str] | None = None
    group: str | None = None
    job_type: str | None = None
    mode: Literal["online", "offline"] = "online"
    api_key: str | None = Field(default_factory=lambda: os.environ.get("WANDB_API_KEY"))


class WandbBackend(TrackingBackend):
    """Normalize native W&B runs without importing W&B eagerly.

    Args:
        config: Project configuration.
        entity: Optional W&B entity.
        project: Required W&B project.
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
        config: HirakuConfig,
        *,
        entity: str | None = None,
        project: str = "",
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
            config: Project configuration.
            entity: Optional W&B entity.
            project: Required W&B project.
            id: Optional W&B run ID.
            experiment_name: Optional W&B experiment name.
            notes: Optional W&B run notes.
            tags: Optional W&B run tags.
            group: Optional W&B run group.
            job_type: Optional W&B run job type.
            mode: W&B run mode; defaults to "online".
            api_key: Optional W&B API key.

        Raises:
            hiraku.core.errors.BackendError: If any settings has conflicts.
        """
        self.config = config
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
        self.entity = settings.entity
        self.project = settings.project
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

    @classmethod
    def _normalize_run(cls, run: Any) -> Run:
        """Convert one W&B public run object into Hiraku's normalized model."""
        from ..core.run import Run

        config = cls._run_config(run)
        return Run(
            id=run.id,
            metrics=dict(run.summary or {}),
            params=cls._run_params(run),
            record_id=config.get("hiraku.record_id"),
            commit=config.get("hiraku.commit"),
            timestamp=run.created_at,
            entrypoint=config.get("hiraku.entrypoint"),
            url=run.url,
            raw=cls._run_raw(run),
        )

    def _read_source_runs(self, **options: Any) -> list[Run]:
        try:
            runs = self._get_runs()
            return [self._normalize_run(run) for run in runs]
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(f"wandb backend query failed: {exc}") from exc
