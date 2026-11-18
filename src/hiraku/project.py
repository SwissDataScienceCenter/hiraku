"""Filesystem provisioning for Hiraku projects."""

from __future__ import annotations

import json
import shutil
import tomllib
from contextlib import ExitStack
from importlib.resources import as_file, files
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .errors import (
    ProjectExistsError,
    ProjectNotFoundError,
    ValidationError,
)
from .io import atomic_write, dump_yaml, load_yaml_mapping

if TYPE_CHECKING:
    from .record import Vault


class Project(BaseModel):
    """
    A project, its persisted settings, and its managed files.

    Attributes:
        root: Absolute project root directory.
        hiraku_dir_name: Name of the managed directory for Hiraku data.

    Args:
        root: Project directory containing the settings file and managed data.
        **data: Persisted project settings to validate and load.
    """

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        validate_assignment=True,
    )

    root: Path = Field(exclude=True)
    hiraku_dir_name: str = Field(
        default="hiraku",
        alias="hiraku_dir",
        min_length=1,
        exclude=True,
        frozen=True,
    )

    def __init__(self, root: Path | str, **data: Any) -> None:
        """Create a project model from its root and persisted settings.

        Args:
            root: Project directory.
            **data: Project settings validated by Pydantic.
        """
        super().__init__(root=root, **data)

    @field_validator("root", mode="before")
    @classmethod
    def resolve_root(cls, value: Path | str) -> Path:
        """Expand and resolve the project root."""
        return Path(value).expanduser().resolve()

    @model_validator(mode="after")
    def validate_hiraku_dir(self) -> Project:
        """Validate relative managed paths while allowing absolute locations."""
        raw = Path(self.hiraku_dir_name)
        if raw.is_absolute():
            return self
        if ".." in raw.parts:
            raise ValueError("hiraku_dir cannot contain parent traversal")
        resolved = (self.root / raw).resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(
                f"hiraku_dir resolves outside project root: {resolved}"
            ) from exc
        return self

    @classmethod
    def load(cls, path: Path | str) -> Project:
        """Load a project from its root-level ``hiraku.yml`` file.

        Args:
            path: Path to the project configuration file.

        Returns:
            The project rooted at the configuration file's parent directory.

        Raises:
            hiraku.errors.ValidationError: If the file or its settings are invalid.
        """
        marker = Path(path).expanduser().resolve()
        data = load_yaml_mapping(marker)
        try:
            project = cls(marker.parent, **data)
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                f"Invalid project configuration {marker}: {exc}"
            ) from exc
        if marker.name != "hiraku.yml":
            raise ValidationError(f"Expected hiraku.yml: {marker}")
        return project

    @classmethod
    def discover(cls, start: Path | str | None = None) -> Project:
        """Find the nearest project by searching ``start`` and its parents.

        Args:
            start: Starting directory or file. Defaults to the current directory.

        Returns:
            The nearest discovered project.

        Raises:
            hiraku.errors.ProjectNotFoundError: If no project is found.
        """
        candidate = Path.cwd() if start is None else Path(start)
        candidate = candidate.expanduser().absolute()
        if candidate.is_file():
            if candidate.name == "hiraku.yml":
                return cls.load(candidate)
            candidate = candidate.parent
        for directory in (candidate, *candidate.parents):
            default_marker = directory / "hiraku.yml"
            if default_marker.is_file():
                return cls.load(default_marker)
        raise ProjectNotFoundError(f"No hiraku.yml found from {candidate}")

    @property
    def hiraku_dir(self) -> Path:
        """Return the resolved directory for Hiraku-managed project data."""
        raw = Path(self.hiraku_dir_name)
        if raw.is_absolute():
            return raw.expanduser().resolve(strict=False)
        resolved = (self.root / raw).resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValidationError(
                f"hiraku_dir resolves outside project root: {resolved}"
            ) from exc
        return resolved

    @property
    def path(self) -> Path:
        """Return the path to the project's root-level ``hiraku.yml`` file."""
        return self.root / "hiraku.yml"

    @property
    def records_dir(self) -> Path:
        """Return the directory containing research record files."""
        return self.hiraku_dir / "records"

    @property
    def vault(self) -> Vault:
        """Return this project's filesystem-backed record collection."""
        from .record import Vault

        return Vault(root=self.records_dir)

    @property
    def leaderboards_dir(self) -> Path:
        """Return the directory containing named leaderboards."""
        return self.hiraku_dir / "leaderboards"

    @property
    def workflows_dir(self) -> Path:
        """Return the directory containing installed workflows."""
        return self.hiraku_dir / "workflows"

    @property
    def jobs_dir(self) -> Path:
        """Return the local directory for submission history."""
        return self.root / ".hiraku" / "jobs"

    @property
    def psij_dir(self) -> Path:
        """Return the local directory for PSI/J launcher and executor files."""
        return self.root / ".hiraku" / "psij"

    def to_dict(self) -> dict[str, Any]:
        """Return the persisted project settings."""
        return {
            "hiraku_dir": self.hiraku_dir_name,
        }

    def save(self) -> None:
        """Atomically save the project's settings to its root-level ``hiraku.yml``.

        Raises:
            hiraku.errors.ValidationError: If the configuration cannot be written.
        """
        try:
            atomic_write(self.path, dump_yaml(self.to_dict()))
        except OSError as exc:
            raise ValidationError(
                f"Cannot save configuration at {self.path}: {exc}"
            ) from exc

    @classmethod
    def create(
        cls,
        root: Path | str = ".",
        *,
        install_skill: bool = True,
        install_mcp: bool = True,
        template: Path | str | None = None,
        example: str | None = None,
        results: bool = False,
        with_generator: bool = False,
    ) -> Project:
        """Create a project and install its selected resources.

        Args:
            root: Directory to initialize.
            install_skill: Whether to install all bundled Hiraku skills.
            install_mcp: Whether to add the project MCP server configuration.
            template: Optional directory whose contents are copied into the project.
            example: Optional packaged example name to initialize from.
            results: Whether to include the example's prepared records and runs.
            with_generator: Whether to copy the example's result generator.

        Returns:
            The initialized project.

        Raises:
            hiraku.errors.ProjectExistsError: If ``root`` already has a project.
            hiraku.errors.ValidationError: If settings or resources are invalid.
        """
        root = Path(root).expanduser().resolve()
        if (root / "hiraku.yml").exists():
            raise ProjectExistsError(f"Hiraku project already exists at {root}")
        if template and example:
            raise ValidationError("template and example are mutually exclusive")
        if (results or with_generator) and not example:
            raise ValidationError("results requires an example")

        resources = files("hiraku.resources")
        with ExitStack() as stack:
            overlay = None
            result_overlay = None
            if template is not None:
                overlay = Path(template).expanduser().resolve()
            elif example is not None:
                if (
                    not example
                    or example in {".", ".."}
                    or Path(example).name != example
                    or "\\" in example
                ):
                    raise ValidationError("Invalid example name")
                bundle = resources / "examples" / example
                if not (bundle / "src").is_dir():
                    try:
                        available = sorted(
                            item.name
                            for item in (resources / "examples").iterdir()
                            if (item / "src").is_dir()
                        )
                    except OSError:
                        available = []
                    names = ", ".join(available) or "none"
                    raise ValidationError(
                        f"Unknown example {example!r}; available examples: {names}"
                    )
                overlay = stack.enter_context(as_file(bundle / "src"))
                if results:
                    if not (bundle / "results").is_dir():
                        raise ValidationError(
                            f"Example {example!r} has no results to include"
                        )
                    result_overlay = stack.enter_context(as_file(bundle / "results"))

            data = {}
            for source in (overlay, result_overlay):
                if source is not None:
                    if not source.is_dir():
                        raise FileNotFoundError(source)
                    config_file = source / "hiraku.yml"
                    if config_file.is_file():
                        data.update(load_yaml_mapping(config_file))
            try:
                project = cls(root, **data)
            except (TypeError, ValueError) as exc:
                raise ValidationError(f"Invalid project configuration: {exc}") from exc
            root.mkdir(parents=True, exist_ok=True)
            project.records_dir.mkdir(parents=True, exist_ok=True)
            project.workflows_dir.mkdir(parents=True, exist_ok=True)
            project.leaderboards_dir.mkdir(parents=True, exist_ok=True)
            (root / ".hiraku").mkdir(parents=True, exist_ok=True)

            with as_file(resources / "workflows") as source:
                shutil.copytree(source, project.workflows_dir, dirs_exist_ok=True)

            # Install the bundled Hiraku skills
            if install_skill:
                with as_file(resources / "skills") as source:
                    for destination in (
                        root / ".agents/skills",  # Generic agents
                        root / ".claude/skills",  # Claude Code
                    ):
                        shutil.copytree(source, destination, dirs_exist_ok=True)

            for source in (overlay, result_overlay):
                if source is not None:
                    shutil.copytree(
                        source,
                        root,
                        dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("hiraku.yml"),
                    )

            # Copy the example's result generator if requested
            if with_generator:
                generator = resources / "examples" / example / "generate_results.py"
                with as_file(generator) as source:
                    shutil.copy2(source, root / "generate_results.py")

            project.save()

            # Install the MCP server configuration in supported project agent configs
            if install_mcp:
                project.install_mcp()
            return project

    def _ignore_codex_config(self) -> None:
        """Keep local Codex MCP settings out of the project repository."""
        path = self.root / ".gitignore"
        content = path.read_text(encoding="utf-8") if path.exists() else ""
        entry = "/.codex/config.toml"
        if entry in content.splitlines():
            return
        separator = "" if not content or content.endswith("\n") else "\n"
        atomic_write(path, f"{content}{separator}{entry}\n")

    def install_mcp(self) -> None:
        """Register the stdio server in supported project agent configs.

        Claude, Pi, and Codex configuration files are merged while preserving
        other configured MCP servers.
        """
        root = self.root
        command = "uv" if (root / "uv.lock").is_file() else "hiraku"
        args = (
            ["run", "hiraku", "mcp", "serve"]
            if command == "uv"
            else [
                "mcp",
                "serve",
            ]
        )
        server = {"command": command, "args": args}

        for path in (root / ".mcp.json", root / ".pi" / "mcp.json"):
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            data.setdefault("mcpServers", {}).setdefault("hiraku", server)
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(path, json.dumps(data, indent=2) + "\n")

        path = root / ".codex" / "config.toml"
        content = path.read_text(encoding="utf-8") if path.exists() else ""
        try:
            codex_config = tomllib.loads(content)
        except tomllib.TOMLDecodeError as exc:
            raise ValidationError(
                f"Cannot merge MCP settings in {path}: {exc}"
            ) from exc
        mcp_servers = codex_config.get("mcp_servers", {})
        inline_mcp_servers = any(
            line.lstrip().startswith("mcp_servers =") for line in content.splitlines()
        )
        if not isinstance(mcp_servers, dict) or inline_mcp_servers:
            raise ValidationError(
                f"Cannot merge MCP settings in {path}: [mcp_servers.hiraku] table "
                "cannot be added when mcp_servers is an inline value"
            )
        if "hiraku" not in mcp_servers:
            entry = (
                "[mcp_servers.hiraku]\n"
                f"command = {json.dumps(command)}\n"
                f"args = {json.dumps(args)}\n"
            )
            new_content = (
                content.rstrip() + "\n\n" + entry if content.strip() else entry
            )
            if tomllib.loads(new_content)["mcp_servers"]["hiraku"] != server:
                raise ValidationError(f"Cannot merge MCP settings in {path}")
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(path, new_content)

        self._ignore_codex_config()
