"""Project discovery, initialization, and configuration."""

from __future__ import annotations

import warnings
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    model_validator,
)

from .errors import BackendError, ProjectNotFoundError, ValidationError
from .io import atomic_write, dump_yaml, load_yaml_mapping


class HirakuConfig(BaseModel):
    """Discovered Hiraku project and validated configuration.

    Attributes:
        path: Path to the project configuration file.
        root: Project root directory.
        hiraku_dir_name: Relative state directory name.
        default_dispatch: Default command dispatcher.
        tracking_backend: Project-wide run tracking backend; defaults to CSV.

    Raises:
        pydantic.ValidationError: If configuration values are invalid or inconsistent.

    Note:
        Runtime paths are resolved from ``root`` and excluded from YAML serialization.

    """

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "hiraku_dir": "hiraku",
        "default_dispatch": None,
        "tracking_backend": "csv",
    }
    model_config = ConfigDict(
        extra="allow", validate_assignment=True, populate_by_name=True
    )

    path: Path = Field(exclude=True)
    root: Path = Field(exclude=True)
    hiraku_dir_name: str = Field(
        default=DEFAULTS["hiraku_dir"], alias="hiraku_dir", min_length=1
    )
    default_dispatch: str | None = Field(
        default=DEFAULTS["default_dispatch"], min_length=1
    )
    tracking_backend: str | dict[str, Any] = DEFAULTS["tracking_backend"]
    _hiraku_dir: Path = PrivateAttr()

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "active_record_id":
            raise ValidationError("Active record selection is no longer supported")
        if name == "command":
            raise ValidationError(
                f"{name} must be configured on a record, not hiraku.yml"
            )
        super().__setattr__(name, value)

    @model_validator(mode="before")
    @classmethod
    def reject_record_defaults(cls, values: Any) -> Any:
        """Keep commands on records rather than project configuration."""
        if not isinstance(values, Mapping):
            return values
        raw = dict(values)
        if "active_record_id" in raw:
            raw.pop("active_record_id")
            warnings.warn(
                "active_record_id in hiraku.yml is ignored and should be removed; "
                "select records explicitly",
                UserWarning,
                stacklevel=2,
            )
        if "default_program" in raw:
            raise ValidationError(
                "Configuration key 'default_program' is no longer supported: "
                "programs were removed. Remove the key from hiraku.yml."
            )
        misplaced = {"command"}.intersection(raw)
        if misplaced:
            names = ", ".join(sorted(misplaced))
            raise ValidationError(
                f"{names} must be configured on a record, not hiraku.yml"
            )
        return raw

    @model_validator(mode="after")
    def validate_config(self) -> HirakuConfig:
        """Validate backend consistency and resolve the state directory."""
        raw = Path(self.hiraku_dir_name)
        if raw.is_absolute():
            resolved = raw.resolve(strict=False)
        else:
            if ".." in raw.parts:
                raise ValidationError("hiraku_dir cannot contain parent traversal")
            resolved = (self.root / raw).resolve(strict=False)
        try:
            resolved.relative_to(self.root.resolve())
        except ValueError as exc:
            raise ValidationError(
                f"hiraku_dir resolves outside project root {self.root}: {resolved}"
            ) from exc
        object.__setattr__(self, "_hiraku_dir", resolved)
        from .tracking.factory import validate_backend_config

        try:
            validate_backend_config(self.tracking_backend)
        except BackendError as exc:
            raise ValidationError(
                f"Invalid tracking_backend in hiraku.yml: {exc}"
            ) from exc
        return self

    @classmethod
    def load(cls, path: Path | str) -> HirakuConfig:
        """Load and validate a project configuration from YAML.

        Args:
            path: Configuration file path.

        Returns:
            A validated project configuration.

        Raises:
            hiraku.errors.ValidationError: If the file is unreadable or invalid.
        """
        marker = Path(path).expanduser().absolute()
        return cls(path=marker, root=marker.parent, **load_yaml_mapping(marker))

    @classmethod
    def discover(cls, start: Path | str | None = None) -> HirakuConfig:
        """Discover the nearest project configuration.

        Args:
            start: Directory or file from which to search upward. Defaults to the
                current working directory.

        Returns:
            The nearest validated configuration.

        Raises:
            hiraku.errors.ProjectNotFoundError: If no configuration is found.
        """
        candidate = Path.cwd() if start is None else Path(start)
        candidate = candidate.expanduser().absolute()
        if candidate.is_file():
            candidate = candidate.parent
        for directory in (candidate, *candidate.parents):
            marker = directory / "hiraku.yml"
            if marker.is_file():
                return cls.load(marker)
        raise ProjectNotFoundError(f"No hiraku.yml found from {candidate}")

    @classmethod
    def init(
        cls,
        root: Path | str = ".",
        *,
        install_skill: bool = True,
        template: Path | str | None = None,
        example: str | None = None,
        results: bool = False,
        with_generator: bool = False,
    ) -> HirakuConfig:
        """Initialize a new Hiraku project.

        Args:
            root: Directory to initialize.
            install_skill: Whether to install the repository-local skill for
                supported coding agents.
            template: Optional local directory copied over the initialized project.
            example: Optional packaged example name whose ``src`` tree is copied.
            results: Include the example's prepared records and tracked runs.
            with_generator: Copy the example's optional generation script.

        Returns:
            The initialized configuration.

        Raises:
            hiraku.errors.ProjectExistsError: If the project is already initialized.
        """
        from .project import initialize_project

        return initialize_project(
            root,
            install_skill=install_skill,
            template=template,
            example=example,
            results=results,
            with_generator=with_generator,
        )

    def save(self) -> None:
        """Persist validated configuration to its YAML file.

        Raises:
            hiraku.errors.ValidationError: If the configuration cannot be written.

        Example:
            ```python
            config = HirakuConfig.discover()
            config.save()
            ```
        """
        try:
            values = self.to_dict()
            if self.path.exists():
                old = load_yaml_mapping(self.path)
                if "active_record_id" in old:
                    values["active_record_id"] = old["active_record_id"]
            atomic_write(self.path, dump_yaml(values))
        except OSError as exc:
            raise ValidationError(
                f"Cannot save configuration at {self.path}: {exc}"
            ) from exc

    @property
    def hiraku_dir(self) -> Path:
        """Return the resolved Hiraku state directory."""
        return self._hiraku_dir

    def to_dict(self) -> dict[str, Any]:
        """Return user-managed fields suitable for YAML persistence.

        Returns:
            A serializable mapping excluding runtime paths and private state.

        Example:
            ``yaml.safe_dump(config.to_dict())``
        """
        return self.model_dump(
            by_alias=True,
            exclude={"path", "root", "_hiraku_dir"},
            exclude_none=False,
        )

    @property
    def records_dir(self) -> Path:
        """Return the directory containing research records."""
        return self.hiraku_dir / "records"

    @property
    def workflows_dir(self) -> Path:
        """Return the directory containing project workflows."""
        return self.hiraku_dir / "workflows"

    @property
    def dispatch_dir(self) -> Path:
        """Return the directory containing command dispatcher scripts."""
        return self.hiraku_dir / "dispatch"
