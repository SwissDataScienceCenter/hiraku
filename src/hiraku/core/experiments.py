"""Core operations for research experiments."""

from __future__ import annotations

from .config import HirakuConfig
from .errors import ValidationError
from .git import GitRepository
from .record import load_record


def commit_implementation(
    record_id: str,
    paths: list[str],
    message: str,
    *,
    config: HirakuConfig | None = None,
) -> dict[str, object]:
    """Commit selected implementation paths and bind the commit to an experiment."""
    selected = config or HirakuConfig.discover()
    record = load_record(record_id, config=selected)
    if record.type != "experiment":
        raise ValidationError("Implementation requires an experiment record")
    if not paths or not message.strip():
        raise ValidationError("paths and message must be non-empty")

    git = GitRepository(selected)
    resolved_paths: list[str] = []
    for path in paths:
        candidate = (selected.root / path).resolve()
        try:
            candidate.relative_to(selected.root.resolve())
        except ValueError as exc:
            raise ValidationError(f"Path is outside the project: {path}") from exc
        if (
            candidate == selected.hiraku_dir
            or selected.hiraku_dir in candidate.parents
            or ".git" in candidate.parts
        ):
            raise ValidationError(
                "Hiraku state and Git metadata cannot be implementation paths"
            )
        if not candidate.is_file():
            raise ValidationError(f"Implementation file does not exist: {path}")
        resolved_paths.append(str(candidate.relative_to(selected.root.resolve())))

    staged_paths = {
        path
        for path in git._git("diff", "--cached", "--name-only", "-z").stdout.split("\0")
        if path
    }
    unrelated_staged = staged_paths.difference(resolved_paths)
    if unrelated_staged:
        raise ValidationError(
            f"Refusing to commit unrelated staged paths: {sorted(unrelated_staged)}"
        )

    git._git("add", "--", *resolved_paths)
    git._git("diff", "--cached", "--check")
    changed_paths = {
        path
        for path in git._git("diff", "--cached", "--name-only", "-z").stdout.split("\0")
        if path
    }
    if not changed_paths:
        untracked_paths = [
            path
            for path in resolved_paths
            if git._git("ls-files", "--error-unmatch", path, check=False).returncode
            != 0
        ]
        if untracked_paths:
            raise ValidationError(
                "No implementation changes were staged and these paths are not "
                f"tracked: {untracked_paths}"
            )
        record.commit = git.head
        record.status = "implemented"
        record.save()
        return {
            "record_id": record.id,
            "commit": record.commit,
            "committed_paths": [],
            "bound_paths": resolved_paths,
        }

    git._git("-c", "commit.gpgSign=false", "commit", "-m", message)
    commit = git.head
    record.commit = commit
    record.status = "implemented"
    record.save()
    return {
        "record_id": record.id,
        "commit": commit,
        "committed_paths": resolved_paths,
    }
