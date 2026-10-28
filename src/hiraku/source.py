"""Prepare a reproducible Git source tree for a job."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .errors import GitError, JobError
from .git import GitRepository


@dataclass(frozen=True)
class SourceSnapshot:
    """Prepared source tree and provenance independent of its execution target."""

    path: Path
    base_revision: str
    digest: str
    dirty: bool


def require_clean(repository: GitRepository) -> str:
    """Require a clean Git tree and return its current HEAD."""
    state = repository._git("status", "--porcelain", "--untracked-files=normal").stdout
    if state:
        raise JobError(
            "Cannot submit a job from a dirty Git working tree; "
            "commit or discard changes first"
        )
    return repository.resolve("HEAD")


def prepare_source(
    repository: GitRepository,
    destination: Path,
    revision: str | None = None,
) -> SourceSnapshot:
    """Capture a commit after verifying the repository has no local changes."""
    initial_head = require_clean(repository)
    base_revision = (
        repository.resolve(revision) if revision else repository.resolve("HEAD")
    )
    destination.mkdir(parents=True, exist_ok=False)
    digest = _prepare_revision(repository, base_revision, destination)
    final_state = repository._git(
        "status", "--porcelain", "--untracked-files=normal"
    ).stdout
    if final_state or repository.resolve("HEAD") != initial_head:
        raise JobError("Git state changed while preparing source; retry submission")
    return SourceSnapshot(destination, base_revision, digest, False)


def _prepare_revision(
    repository: GitRepository, revision: str, destination: Path
) -> str:
    """Copy an exact detached checkout without its .git metadata."""
    with tempfile.TemporaryDirectory(prefix="hiraku-checkout-") as temporary:
        checkout = Path(temporary) / "source"
        result = repository._git(
            "worktree", "add", "--detach", str(checkout), revision, check=False
        )
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip()
            raise GitError(f"Could not prepare source revision {revision}: {detail}")
        try:
            return _copy_checkout(checkout, destination)
        finally:
            repository._git("worktree", "remove", "--force", str(checkout), check=False)


def _copy_checkout(checkout: Path, destination: Path) -> str:
    digest = hashlib.sha256()
    for source in sorted(checkout.rglob("*")):
        relative = source.relative_to(checkout)
        if relative.parts[0] == ".git":
            continue
        target = destination / relative
        if source.is_symlink():
            link = os.readlink(source)
            if Path(link).is_absolute() or not _within(
                (source.parent / link).resolve(strict=False), checkout
            ):
                raise JobError(f"Source symlink escapes repository: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(link)
            digest.update(
                relative.as_posix().encode() + b"\0link\0" + link.encode() + b"\0"
            )
        elif source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            before = source.stat()
            content = source.read_bytes()
            after = source.stat()
            if (
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_mode,
            ) != (
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_mode,
            ):
                raise JobError(f"Source changed while snapshotting: {relative}")
            with target.open("wb") as output:
                output.write(content)
            executable = bool(after.st_mode & 0o111)
            target.chmod(0o755 if executable else 0o644)
            digest.update(
                relative.as_posix().encode()
                + b"\0"
                + (b"x" if executable else b"-")
                + b"\0"
                + content
                + b"\0"
            )
        else:
            raise JobError(f"Unsupported source entry: {relative}")
    for link in destination.rglob("*"):
        if link.is_symlink() and not _within(link.resolve(strict=False), destination):
            raise JobError(f"Source symlink escapes repository: {link}")
    if not any(destination.iterdir()):
        raise JobError("Cannot prepare an empty source snapshot")
    return digest.hexdigest()


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root.resolve())
        return True
    except ValueError:
        return False
