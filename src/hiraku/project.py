"""Filesystem provisioning for Hiraku projects."""

from __future__ import annotations

import shutil
from contextlib import suppress
from importlib.resources import as_file, files
from pathlib import Path
from typing import TYPE_CHECKING

from .errors import GitError, HirakuError, ProjectExistsError, ValidationError
from .io import atomic_write, dump_yaml

if TYPE_CHECKING:
    from .config import HirakuConfig


class ProjectInitializer:
    """Create a new Hiraku project and install its bundled resources."""

    @classmethod
    def create(
        cls,
        root: Path | str = ".",
        *,
        install_skill: bool = True,
        tracking_backend: str | None = None,
        ignore_git: bool = True,
    ) -> HirakuConfig:
        """Create and initialize a project directory.

        Args:
            root: Root directory where the project will be created.
            install_skill: Whether to install repository-local skill resources.
            tracking_backend: Optional default tracking backend name.
            ignore_git: Skip validation that root is a Git repository root.
                Defaults to true for programmatic initialization.

        Returns:
            The initialized `hiraku.config.HirakuConfig`.

        Raises:
            hiraku.errors.ProjectExistsError: If the project marker already exists.
            hiraku.errors.ValidationError: If provisioning fails.
        """
        from .config import HirakuConfig
        from .git import GitRepository

        project_root = Path(root).expanduser().absolute()
        if not ignore_git:
            try:
                repository = GitRepository(project_root)
            except GitError as exc:
                raise ValidationError(
                    f"Hiraku must be initialized at a Git repository root: "
                    f"{project_root} is not in a Git repository"
                ) from exc
            if repository.root != project_root.resolve():
                raise ValidationError(
                    f"Hiraku must be initialized at the Git repository root "
                    f"{repository.root}, not {project_root}"
                )
        marker = project_root / "hiraku.yml"
        if marker.exists():
            raise ProjectExistsError(f"Hiraku project already exists at {marker}")
        created_files: list[Path] = []
        created_dirs: list[Path] = []
        root_existed = project_root.exists()
        data = dict(HirakuConfig.DEFAULTS)
        if tracking_backend is not None:
            data["tracking_backend"] = {"type": tracking_backend}
        try:
            project_root.mkdir(parents=True, exist_ok=True)
            if not root_existed:
                created_dirs.append(project_root)
            atomic_write(marker, dump_yaml(data))
            created_files.append(marker)
            config = HirakuConfig(path=marker, root=marker.parent, **data)
            for directory in (
                config.tasks_dir,
                config.programs_dir,
                config.actions_dir,
                config.contexts_dir,
                config.dispatch_dir,
            ):
                if not directory.exists():
                    directory.mkdir(parents=True)
                    created_dirs.append(directory)
            resource_root = files("hiraku.resources")
            copies = [
                (resource_root / "programs", config.programs_dir),
                (resource_root / "actions", config.actions_dir),
                (resource_root / "dispatch", config.dispatch_dir),
            ]
            if install_skill:
                copies.append(
                    (resource_root / "skill", project_root / ".agents/skills/hiraku")
                )
            for source, target in copies:
                with as_file(source) as source_path:
                    cls._copy_resource_tree(
                        source_path, target, created_files, created_dirs
                    )
            for name in ("launch.sh", "worktree.sh"):
                dispatcher = config.dispatch_dir / name
                dispatcher.chmod(dispatcher.stat().st_mode | 0o111)
            return config
        except Exception as exc:
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
                shutil.copyfile(item, destination)
                created_files.append(destination)
