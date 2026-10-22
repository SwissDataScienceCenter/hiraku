"""Generic, read-only filesystem discovery for dataset registration.

Discovery lists regular files under explicit roots and produces artifact
descriptors that feed the same registration path as a source-adapter manifest.
It never reads file contents or computes content hashes: each revision is the
observed ``size:mtime`` pair, labelled accurately. Roots are explicit; external
mounts must be named, configured roots, so an absolute path never becomes a
stable identity and relocating a mount does not change an artifact's key.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from pathlib import Path

from ..config import HirakuConfig
from ..errors import ValidationError
from .schema import (
    Artifact,
    Dataset,
    RegistrationManifest,
    normalize_display_path,
)

_NAMESPACE = "filesystem"
_EXCLUDED_DIRS = {".git"}


def external_roots(config: HirakuConfig) -> dict[str, str]:
    """Return the named external roots configured in ``hiraku.yml``.

    The ``dataset_roots`` mapping is ``name -> mount path``. It is preserved as
    configuration, never as identity; only the name enters records.
    """
    raw = config.to_dict().get("dataset_roots", {})
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValidationError("dataset_roots must be a mapping of name to path")
    return {str(name): str(path) for name, path in raw.items()}


def scan_files(
    config: HirakuConfig,
    roots: list[Path],
    external: dict[str, str] | None = None,
    *,
    max_files: int = 100_000,
) -> list[Artifact]:
    """List regular files under explicit roots as artifact descriptors.

    Project-relative roots are resolved against the project root; external roots
    come from ``external``, a mapping of configured root name to mount path.
    Symlinks are skipped, overlapping roots are deduplicated, and a scan that
    would exceed ``max_files`` raises instead of silently truncating.

    Raises:
        hiraku.errors.ValidationError: For an invalid, unconfigured, or
            unbounded root, or an unreadable path.
    """
    if max_files < 1:
        raise ValidationError("max_files must be positive")
    if not roots and not external:
        raise ValidationError("at least one root is required")
    project = config.root.resolve()
    allowed = external or {}
    artifacts: list[Artifact] = []
    seen: set[str] = set()

    for root in roots:
        candidate = (project / root).resolve()
        if not candidate.is_relative_to(project):
            raise ValidationError(f"root is outside the project: {root}")
        if not candidate.is_dir():
            raise ValidationError(f"root is not a directory: {root}")
        _walk(candidate, project, None, artifacts, seen, max_files, config)

    for name, mount in sorted(allowed.items()):
        if not name:
            raise ValidationError("external root name must be non-empty")
        candidate = Path(mount).expanduser().resolve()
        if not candidate.is_dir():
            raise ValidationError(f"external root {name!r} is not a directory: {mount}")
        _walk(candidate, candidate, name, artifacts, seen, max_files, config)

    return sorted(artifacts, key=lambda artifact: (artifact.root or "", artifact.key))


def _walk(
    root: Path,
    base: Path,
    name: str | None,
    artifacts: list[Artifact],
    seen: set[str],
    max_files: int,
    config: HirakuConfig,
) -> None:
    hiraku_dir = config.hiraku_dir.resolve()
    exclusions = set(_EXCLUDED_DIRS)

    def onerror(error: OSError) -> None:
        raise ValidationError(f"cannot scan {error.filename}: {error}") from error

    for directory, subdirs, names in os.walk(root, followlinks=False, onerror=onerror):
        subdirs[:] = sorted(
            entry
            for entry in subdirs
            if not (Path(directory) / entry).is_symlink()
            and not _excluded(Path(directory) / entry, base, hiraku_dir, exclusions)
        )
        for entry in sorted(names):
            candidate = Path(directory) / entry
            try:
                info = candidate.lstat()
            except OSError as exc:
                raise ValidationError(f"cannot inspect {candidate}: {exc}") from exc
            if not stat.S_ISREG(info.st_mode):
                continue  # directories and other special files are not artifacts
            if candidate.is_symlink():
                continue
            absolute = str(candidate.resolve())
            if absolute in seen:
                continue  # overlapping roots observe a file once
            seen.add(absolute)
            display_path = candidate.relative_to(base).as_posix()
            artifact_key = display_path if name is None else f"{name}/{display_path}"
            artifacts.append(
                Artifact(
                    key=artifact_key,
                    display_path=normalize_display_path(display_path),
                    root=name,
                    revision=f"{info.st_size}:{info.st_mtime_ns}",
                    size=info.st_size,
                    availability="present",
                )
            )
            if len(artifacts) > max_files:
                raise ValidationError(
                    f"scan exceeds max_files={max_files}; narrow the roots"
                )


def _excluded(
    path: Path, base: Path, hiraku_dir: Path, exclusions: set[str]
) -> bool:
    return path.name in exclusions or path.resolve() == hiraku_dir


def scan_manifest(
    config: HirakuConfig,
    roots: list[Path],
    external: dict[str, str] | None = None,
    *,
    max_files: int = 100_000,
) -> RegistrationManifest:
    """Scan roots and return the versioned manifest they describe.

    The dataset key is the sorted list of root identifiers: project-relative
    paths for project roots and ``external:<name>`` for configured mounts, so
    identity never depends on an absolute path.
    """
    allowed = external or {}
    artifacts = scan_files(config, roots, allowed, max_files=max_files)
    identifiers = sorted([root.as_posix() for root in roots])
    identifiers.extend(sorted(f"external:{name}" for name in allowed))
    dataset = Dataset(
        namespace=_NAMESPACE,
        key=identifiers,
        coverage="exhaustive",
        roots=identifiers,
        artifacts=artifacts,
    )
    manifest = RegistrationManifest(
        source=_NAMESPACE,
        scope={"coverage": "exhaustive", "roots": identifiers},
        datasets=[dataset],
    )
    manifest.content_sha256 = manifest.compute_digest()
    return manifest
