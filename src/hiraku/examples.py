"""Discovery and copy selection for packaged examples."""

from __future__ import annotations

import builtins
import shutil
from collections.abc import Callable
from importlib.resources import as_file, files
from importlib.resources.abc import Traversable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .errors import ProjectExistsError, ValidationError
from .io import load_yaml_mapping


def _examples_root() -> Traversable:
    # Editable installs expose src/ while the examples remain in the checkout.
    source = Path(__file__).resolve().parents[1]
    if source.name == "src" and (source.parent / "pyproject.toml").is_file():
        return source.parent / "examples"
    return files("hiraku.resources") / "examples"


class Example(BaseModel):
    """Manifest for a packaged example."""

    model_config = ConfigDict(extra="forbid")
    title: str = Field(pattern=r"\S")
    description: str = Field(pattern=r"\S")
    results: builtins.list[str] = Field(default_factory=list)
    exclude: builtins.list[str] = Field(default_factory=list)

    @field_validator("results", "exclude")
    @classmethod
    def validate_patterns(cls, patterns: builtins.list[str]) -> builtins.list[str]:
        for pattern in patterns:
            path = Path(pattern)
            if (
                not path.parts
                or path.anchor
                or ".." in path.parts
                or "\\" in pattern
                or any("**" in part and part != "**" for part in path.parts)
            ):
                raise ValueError(f"Expected an example-relative glob: {pattern!r}")
        return patterns

    @classmethod
    def list(cls) -> builtins.list[str]:
        """List available example names."""
        root = _examples_root()
        if not root.is_dir():
            return []
        return sorted(
            item.name
            for item in root.iterdir()
            if (item / ".hiraku-example.yml").is_file()
        )

    @classmethod
    def load(cls, source: Traversable) -> Example:
        """Read the example manifest."""
        with as_file(source / ".hiraku-example.yml") as path:
            try:
                return cls.model_validate(load_yaml_mapping(path))
            except ValueError as exc:
                raise ValidationError(
                    f"Invalid example {source.name!r}: {exc}"
                ) from exc

    @staticmethod
    def source(name: str) -> Traversable:
        """Locate a named example."""
        if not name or name in {".", ".."} or Path(name).name != name or "\\" in name:
            raise ValidationError("Invalid example name")
        source = _examples_root() / name
        if not (source / ".hiraku-example.yml").is_file():
            names = ", ".join(Example.list()) or "none"
            raise ValidationError(
                f"Unknown example {name!r}; available examples: {names}"
            )
        return source

    @classmethod
    def create(
        cls, name: str, destination: Path | str, *, with_results: bool = False
    ) -> Path:
        """Copy the selected files into a new example project."""
        destination = Path(destination).expanduser().resolve()
        marker = destination / "hiraku.yml"
        if marker.exists():
            raise ProjectExistsError(f"Hiraku project already exists at {destination}")
        bundle = cls.source(name)
        manifest = cls.load(bundle)
        with as_file(bundle) as source:
            if destination.is_relative_to(source.resolve()):
                raise ValidationError("Cannot create an example inside its source")
            ignore = manifest._copy_ignore(source, name, with_results=with_results)
            shutil.copytree(source, destination, dirs_exist_ok=True, ignore=ignore)
        return marker

    def _copy_ignore(
        self, root: Path, name: str, *, with_results: bool
    ) -> Callable[[str, builtins.list[str]], set[str]]:
        """Build a copytree filter from the manifest's relative globs."""
        excluded = {root / ".git", root / ".hiraku-example.yml"}
        excluded.update(
            path
            for pattern in self.exclude
            for path in root.glob(pattern)
            if path != root
        )
        results = {
            path
            for pattern in self.results
            for path in root.glob(pattern)
            if path != root
        }
        if with_results:
            files = (
                file
                for path in results
                for file in (path.rglob("*") if path.is_dir() else (path,))
            )
            if not any(
                file.is_file()
                and file not in excluded
                and excluded.isdisjoint(file.parents)
                for file in files
            ):
                raise ValidationError(f"Example {name!r} has no results to include")
        else:
            excluded.update(results)

        def ignore(directory: str, names: list[str]) -> set[str]:
            return {name for name in names if Path(directory) / name in excluded}

        return ignore
