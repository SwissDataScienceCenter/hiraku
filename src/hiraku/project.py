"""Filesystem provisioning for Hiraku projects."""

from __future__ import annotations

import json
import os
import shutil
import tomllib
from contextlib import ExitStack, suppress
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path
from typing import TYPE_CHECKING

from .errors import HirakuError, ProjectExistsError, ValidationError
from .io import atomic_write, dump_yaml, load_yaml_mapping
from .validation import validate_identifier

if TYPE_CHECKING:
    from .config import HirakuConfig


@dataclass(frozen=True, slots=True)
class _InitializationResult:
    config: HirakuConfig
    created_files: tuple[Path, ...]
    created_dirs: tuple[Path, ...]


class ProjectInitializer:
    """Create a new Hiraku project and install its bundled resources."""

    @classmethod
    def create(
        cls,
        root: Path | str = ".",
        *,
        install_skill: bool = True,
        tracking_backend: str | None = None,
        template: Path | str | None = None,
        example: str | None = None,
        results: bool = False,
        with_generator: bool = False,
    ) -> HirakuConfig:
        """Create and initialize a project directory.

        Args:
            root: Root directory where the project will be created.
            install_skill: Whether to install repository-local skill resources for
                supported coding agents.
            tracking_backend: Optional default tracking backend name.
            template: Optional local directory copied over the initialized project.
            example: Optional packaged example name whose ``src`` tree is copied.
            results: Include the example's prepared records and tracked runs.
            with_generator: Copy the example's optional generation script.

        Returns:
            The initialized `hiraku.config.HirakuConfig`.

        Raises:
            hiraku.errors.ProjectExistsError: If the project marker already exists.
            hiraku.errors.ValidationError: If provisioning fails.
        """
        return cls.create_with_report(
            root,
            install_skill=install_skill,
            tracking_backend=tracking_backend,
            template=template,
            example=example,
            results=results,
            with_generator=with_generator,
        ).config

    @classmethod
    def create_with_report(
        cls,
        root: Path | str = ".",
        *,
        install_skill: bool = True,
        tracking_backend: str | None = None,
        template: Path | str | None = None,
        example: str | None = None,
        results: bool = False,
        with_generator: bool = False,
    ) -> _InitializationResult:
        """Initialize a project and report paths created by the operation."""
        from .config import HirakuConfig

        if template is not None and example is not None:
            raise ValidationError("template and example are mutually exclusive")
        if results and example is None:
            raise ValidationError("results requires a packaged example")
        if with_generator and example is None:
            raise ValidationError("with_generator requires a packaged example")
        project_root = Path(root).expanduser().absolute()
        marker = project_root / "hiraku.yml"
        if marker.exists():
            raise ProjectExistsError(f"Hiraku project already exists at {marker}")
        created_files: list[Path] = []
        created_dirs: list[Path] = []
        replaced_files: dict[Path, bytes] = {}
        root_existed = project_root.exists()
        with ExitStack() as stack:
            overlay = cls._resolve_overlay(template, example, stack)
            entries = cls._inventory_overlay(overlay, project_root)
            if with_generator:
                generator = (
                    files("hiraku.resources")
                    / "examples"
                    / example
                    / "generate_results.py"
                )
                if not generator.is_file():
                    raise ValidationError(f"Example {example!r} has no generator")
                destination = project_root / "generate_results.py"
                cls._validate_existing_destination(destination, False)
                entries.append(
                    (
                        stack.enter_context(as_file(generator)),
                        Path("generate_results.py"),
                    )
                )
            result_overlay = None
            if results:
                source = files("hiraku.resources") / "examples" / example / "results"
                if not source.is_dir():
                    raise ValidationError(f"Example {example!r} has no results")
                result_overlay = stack.enter_context(as_file(source))
                entries.extend(cls._inventory_overlay(result_overlay, project_root))
            data = dict(HirakuConfig.DEFAULTS)
            overlay_marker = overlay / "hiraku.yml" if overlay is not None else None
            if overlay_marker is not None and overlay_marker.is_file():
                data = load_yaml_mapping(overlay_marker)
            if result_overlay is not None:
                result_marker = result_overlay / "hiraku.yml"
                if result_marker.is_file():
                    data.update(load_yaml_mapping(result_marker))
            if tracking_backend is not None:
                data["tracking_backend"] = {"type": tracking_backend}
            config = HirakuConfig(path=marker, root=project_root, **data)
            try:
                project_root.mkdir(parents=True, exist_ok=True)
                if not root_existed:
                    created_dirs.append(project_root)
                atomic_write(marker, dump_yaml(config.to_dict()))
                created_files.append(marker)
                for directory in (
                    config.records_dir,
                    config.workflows_dir,
                    config.contexts_dir,
                    config.dispatch_dir,
                ):
                    cls._ensure_directory(directory, created_dirs)
                resource_root = files("hiraku.resources")
                copies = [
                    (resource_root / "workflows", config.workflows_dir),
                    (resource_root / "dispatch", config.dispatch_dir),
                ]
                if install_skill:
                    skill_source = resource_root / "skills/hiraku"
                    canonical_skill = project_root / ".agents/skills/hiraku"
                    copies.append((skill_source, canonical_skill))
                for source, target in copies:
                    with as_file(source) as source_path:
                        cls._copy_resource_tree(
                            source_path, target, created_files, created_dirs
                        )
                if install_skill:
                    cls._link_skill_alias(
                        canonical_skill,
                        project_root / ".claude/skills/hiraku",
                        created_files,
                        created_dirs,
                    )
                cls._apply_overlay(entries, project_root, created_files, created_dirs)
                cls._install_mcp_configs(
                    project_root, created_files, created_dirs, replaced_files
                )
                for name in ("launch.sh", "worktree.sh"):
                    dispatcher = config.dispatch_dir / name
                    if dispatcher.is_file():
                        dispatcher.chmod(dispatcher.stat().st_mode | 0o111)
                return _InitializationResult(
                    config=config,
                    created_files=tuple(dict.fromkeys(created_files)),
                    created_dirs=tuple(dict.fromkeys(created_dirs)),
                )
            except Exception as exc:
                for path, content in replaced_files.items():
                    with suppress(OSError):
                        atomic_write(path, content.decode("utf-8"))
                for path in reversed(created_files):
                    path.unlink(missing_ok=True)
                for path in sorted(
                    created_dirs, key=lambda item: len(item.parts), reverse=True
                ):
                    with suppress(OSError):
                        path.rmdir()
                if isinstance(exc, HirakuError):
                    raise
                raise ValidationError(
                    f"Could not initialize Hiraku project at {project_root}: {exc}"
                ) from exc

    @classmethod
    def _install_mcp_configs(
        cls,
        project_root: Path,
        created_files: list[Path],
        created_dirs: list[Path],
        replaced_files: dict[Path, bytes],
    ) -> None:
        """Register the stdio server for Claude Code and Codex project configs."""
        claude_path = project_root / ".mcp.json"
        claude_data: dict[str, object] = {}
        if claude_path.is_symlink():
            raise ValidationError(
                f"MCP configuration cannot be a symbolic link: {claude_path}"
            )
        if claude_path.exists():
            if not claude_path.is_file():
                raise ValidationError(
                    f"MCP configuration is not a regular file: {claude_path}"
                )
            original = claude_path.read_bytes()
            try:
                parsed = json.loads(original)
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ValidationError(
                    f"Cannot merge MCP configuration {claude_path}: {exc}"
                ) from exc
            if not isinstance(parsed, dict):
                raise ValidationError(
                    f"MCP configuration must be a JSON object: {claude_path}"
                )
            claude_data = parsed
        servers = claude_data.setdefault("mcpServers", {})
        if not isinstance(servers, dict):
            raise ValidationError(f"mcpServers must be a JSON object: {claude_path}")
        if "hiraku" not in servers:
            servers["hiraku"] = {"command": "hiraku", "args": ["mcp"]}
            if claude_path.exists():
                replaced_files[claude_path] = original
            else:
                cls._ensure_directory(claude_path.parent, created_dirs)
                created_files.append(claude_path)
            atomic_write(claude_path, json.dumps(claude_data, indent=2) + "\n")

        codex_path = project_root / ".codex" / "config.toml"
        codex_text = ""
        if codex_path.parent.is_symlink():
            raise ValidationError(
                "MCP configuration directory cannot be a symbolic link: "
                f"{codex_path.parent}"
            )
        if codex_path.is_symlink():
            raise ValidationError(
                f"MCP configuration cannot be a symbolic link: {codex_path}"
            )
        if codex_path.exists():
            if not codex_path.is_file():
                raise ValidationError(
                    f"MCP configuration is not a regular file: {codex_path}"
                )
            original = codex_path.read_bytes()
            try:
                parsed_codex = tomllib.loads(original.decode("utf-8"))
            except (UnicodeError, tomllib.TOMLDecodeError) as exc:
                raise ValidationError(
                    f"Cannot merge MCP configuration {codex_path}: {exc}"
                ) from exc
            if not isinstance(parsed_codex.get("mcp_servers", {}), dict):
                raise ValidationError(f"mcp_servers must be a TOML table: {codex_path}")
            if "hiraku" in parsed_codex.get("mcp_servers", {}):
                return
            replaced_files[codex_path] = original
            codex_text = original.decode("utf-8")
        else:
            cls._ensure_directory(codex_path.parent, created_dirs)
            created_files.append(codex_path)
        suffix = "\n" if codex_text and not codex_text.endswith("\n") else ""
        if codex_text and not codex_text.endswith("\n\n"):
            suffix += "\n"
        codex_text += (
            f'{suffix}[mcp_servers.hiraku]\ncommand = "hiraku"\nargs = ["mcp"]\n'
        )
        # Appending a table cannot extend every valid layout, such as an inline
        # mcp_servers table, so check the result before replacing the file.
        try:
            merged = tomllib.loads(codex_text)["mcp_servers"]["hiraku"]
        except (tomllib.TOMLDecodeError, KeyError, TypeError):
            merged = None
        if merged != {"command": "hiraku", "args": ["mcp"]}:
            raise ValidationError(
                f"Cannot add the hiraku MCP server to {codex_path}; add a "
                '[mcp_servers.hiraku] table with command = "hiraku" and '
                'args = ["mcp"] manually'
            )
        atomic_write(codex_path, codex_text)

    @staticmethod
    def _resolve_overlay(
        template: Path | str | None, example: str | None, stack: ExitStack
    ) -> Path | None:
        if template is not None:
            source = Path(template).expanduser().absolute()
            if source.is_symlink():
                raise ValidationError(
                    f"Template directory cannot be a symbolic link: {source}"
                )
            if not source.is_dir():
                raise ValidationError(f"Template directory not found: {source}")
            if not os.access(source, os.R_OK | os.X_OK):
                raise ValidationError(f"Template directory is not readable: {source}")
            return source
        if example is None:
            return None
        validate_identifier(example, label="example name")
        examples = files("hiraku.resources") / "examples"
        source = examples / example / "src"
        if not source.is_dir():
            available = (
                sorted(
                    item.name
                    for item in examples.iterdir()
                    if item.is_dir() and (item / "src").is_dir()
                )
                if examples.is_dir()
                else []
            )
            suffix = (
                f"; available examples: {', '.join(available)}" if available else ""
            )
            raise ValidationError(f"Unknown example {example!r}{suffix}")
        return stack.enter_context(as_file(source))

    @classmethod
    def _inventory_overlay(
        cls, source: Path | None, project_root: Path
    ) -> list[tuple[Path, Path]]:
        if source is None:
            return []
        entries: list[tuple[Path, Path]] = []
        root = project_root.resolve(strict=False)
        try:
            items = list(source.rglob("*"))
        except OSError as exc:
            raise ValidationError(
                f"Cannot read template directory {source}: {exc}"
            ) from exc
        for item in items:
            if item.is_symlink():
                raise ValidationError(f"Template contains a symbolic link: {item}")
            if not item.is_dir() and not item.is_file():
                raise ValidationError(f"Template contains a special file: {item}")
            relative = item.relative_to(source)
            destination = project_root / relative
            try:
                destination.resolve(strict=False).relative_to(root)
            except ValueError as exc:
                raise ValidationError(
                    f"Template path resolves outside project root: {relative}"
                ) from exc
            cls._validate_existing_destination(destination, item.is_dir())
            entries.append((item, relative))
        return entries

    @staticmethod
    def _validate_existing_destination(destination: Path, source_is_dir: bool) -> None:
        current = destination
        while current != current.parent:
            if current.is_symlink():
                raise ValidationError(
                    f"Template destination uses a symbolic link: {current}"
                )
            current = current.parent
        if not destination.exists():
            return
        if source_is_dir and destination.is_dir():
            return
        raise ValidationError(f"Template would overwrite existing path: {destination}")

    @staticmethod
    def _ensure_directory(directory: Path, created_dirs: list[Path]) -> None:
        missing: list[Path] = []
        current = directory
        while not current.exists():
            missing.append(current)
            current = current.parent
        if not current.is_dir():
            raise ValidationError(f"Initialization path is not a directory: {current}")
        for path in reversed(missing):
            path.mkdir()
            created_dirs.append(path)

    @classmethod
    def _apply_overlay(
        cls,
        entries: list[tuple[Path, Path]],
        project_root: Path,
        created_files: list[Path],
        created_dirs: list[Path],
    ) -> None:
        created = set(created_files)
        for source, relative in entries:
            if relative == Path("hiraku.yml"):
                continue
            destination = project_root / relative
            if source.is_dir():
                cls._ensure_directory(destination, created_dirs)
            elif destination.exists():
                if destination not in created or not destination.is_file():
                    raise ValidationError(
                        f"Template would overwrite existing path: {destination}"
                    )
                shutil.copy2(source, destination)
            else:
                cls._ensure_directory(destination.parent, created_dirs)
                shutil.copy2(source, destination)
                created_files.append(destination)
                created.add(destination)

    @staticmethod
    def _copy_resource_tree(
        source: Path, target: Path, created_files: list[Path], created_dirs: list[Path]
    ) -> None:
        missing: list[Path] = []
        current = target
        while not current.exists():
            missing.append(current)
            current = current.parent
        for directory in reversed(missing):
            directory.mkdir()
            created_dirs.append(directory)
        for item in source.rglob("*"):
            relative = item.relative_to(source)
            destination = target / relative
            if item.is_dir():
                if not destination.exists():
                    destination.mkdir(parents=True)
                    created_dirs.append(destination)
            elif destination.exists():
                raise ValidationError(
                    "Initialization resource would overwrite existing file: "
                    f"{destination}"
                )
            else:
                shutil.copy2(item, destination)
                created_files.append(destination)

    @classmethod
    def _link_skill_alias(
        cls,
        source: Path,
        target: Path,
        created_files: list[Path],
        created_dirs: list[Path],
    ) -> None:
        """Link a harness-specific skill path to the canonical project skill."""
        if target.exists() or target.is_symlink():
            raise ValidationError(
                f"Initialization resource would overwrite existing path: {target}"
            )
        cls._ensure_directory(target.parent, created_dirs)
        link_target = Path(os.path.relpath(source, start=target.parent))
        try:
            target.symlink_to(link_target, target_is_directory=True)
        except (NotImplementedError, OSError):
            # Some Windows configurations do not allow creating directory links.
            # Keep initialization usable there, with a generated copy as fallback.
            cls._copy_resource_tree(source, target, created_files, created_dirs)
        else:
            created_files.append(target)
