"""Prepare a reproducible Git source tree for a job."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .errors import GitError, JobError
from .git import GitRepository


@dataclass(frozen=True)
class SourceSnapshot:
    """Prepared source tree and provenance independent of its execution target."""

    path: Path
    base_revision: str
    digest: str
    dirty: bool


def prepare_source(
    repository: GitRepository,
    destination: Path,
    revision: str | None = None,
) -> SourceSnapshot:
    """Capture either a pinned commit or current Git-visible working tree."""
    base_revision = repository.resolve(revision) if revision else repository.head
    destination.mkdir(parents=True, exist_ok=False)
    if revision:
        digest = _prepare_revision(repository, base_revision, destination)
        return SourceSnapshot(destination, base_revision, digest, False)

    state = repository._git("status", "--porcelain", "--untracked-files=normal").stdout
    digest = _prepare_working_tree(repository, destination, state)
    if repository.head != base_revision:
        raise JobError("Git HEAD changed while snapshotting; retry submission")
    return SourceSnapshot(
        destination,
        base_revision,
        digest,
        bool(state),
    )


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


def _prepare_working_tree(
    repository: GitRepository, destination: Path, initial_state: str
) -> str:
    root = repository.root
    entries = repository._git(
        "ls-files", "--cached", "--others", "--exclude-standard", "-z"
    ).stdout
    digest = hashlib.sha256()
    signatures: dict[str, tuple[int, int, int, int] | str] = {}
    for entry in entries.split("\0"):
        if not entry:
            continue
        relative = PurePosixPath(entry)
        if relative.is_absolute() or ".." in relative.parts:
            raise GitError(f"Unsafe path returned by Git: {entry!r}")
        source = root.joinpath(*relative.parts)
        target = destination.joinpath(*relative.parts)
        if not source.exists() and not source.is_symlink():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            link = os.readlink(source)
            if Path(link).is_absolute() or not _within(
                (source.parent / link).resolve(strict=False), root
            ):
                raise JobError(f"Source symlink escapes repository: {entry!r}")
            target.symlink_to(link)
            data = link.encode()
            mode = b"link"
            signatures[entry] = link
        elif source.is_file():
            before = source.stat()
            content = source.read_bytes()
            after = source.stat()
            signature = (after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode)
            initial = (
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_mode,
            )
            if initial != signature:
                raise JobError(f"Source changed while snapshotting: {entry!r}")
            target.write_bytes(content)
            target.chmod(0o755 if after.st_mode & 0o111 else 0o644)
            data = content
            mode = b"x" if after.st_mode & 0o111 else b"-"
            signatures[entry] = signature
        else:
            continue
        digest.update(entry.encode() + b"\0" + mode + b"\0" + data + b"\0")
    final_state = repository._git(
        "status", "--porcelain", "--untracked-files=normal"
    ).stdout
    if initial_state != final_state or not entries:
        raise JobError("Source changed while snapshotting; retry submission")
    for entry, signature in signatures.items():
        source = root.joinpath(*PurePosixPath(entry).parts)
        if source.is_symlink():
            unchanged = os.readlink(source) == signature
        elif source.is_file():
            stat = source.stat()
            unchanged = (
                stat.st_ino,
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_mode,
            ) == signature
        else:
            unchanged = False
        if not unchanged:
            raise JobError(f"Source changed while snapshotting: {entry!r}")
    return digest.hexdigest()
