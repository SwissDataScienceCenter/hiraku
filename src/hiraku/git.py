"""Optional Git inspection, setup, and metadata versioning."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from .config import HirakuConfig
from .errors import GitError, ValidationError
from .io import atomic_write


class GitRepository:
    """The Git repository containing a Hiraku project."""

    def __init__(self, config: HirakuConfig | Path | str | None = None) -> None:
        require_git()
        if isinstance(config, HirakuConfig):
            start = config.root
        else:
            start = Path.cwd() if config is None else Path(config)
        result = self._execute(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"], cwd=None
        )
        self.root = Path(result.stdout.strip()).resolve()
        project_root = Path(start).resolve()
        try:
            project_root.relative_to(self.root)
        except ValueError as exc:
            raise GitError(
                f"Project {project_root} is outside Git repository {self.root}"
            ) from exc

    @staticmethod
    def _execute(
        arguments: Sequence[str], *, cwd: Path | None, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                list(arguments), cwd=cwd, text=True, capture_output=True, check=False
            )
        except OSError as exc:
            raise GitError(
                f"Could not execute Git command {list(arguments)!r}: {exc}"
            ) from exc
        if check and result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise GitError(f"Git command failed ({' '.join(arguments)}): {detail}")
        return result

    def _git(
        self, *arguments: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return self._execute(["git", *arguments], cwd=self.root, check=check)

    def resolve(self, ref: str) -> str:
        if not isinstance(ref, str) or not ref:
            raise GitError(f"Invalid Git ref: {ref!r}")
        return self._git("rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()

    @property
    def current_branch(self) -> str | None:
        result = self._git("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
        return result.stdout.strip() if result.returncode == 0 else None


_START = "# BEGIN Hiraku Git setup"
_END = "# END Hiraku Git setup"


def require_git() -> None:
    """Fail before provisioning when an explicitly requested Git tool is absent."""
    if shutil.which("git") is None:
        raise GitError("Git executable was not found on PATH")


def init_code_repository(config: HirakuConfig) -> None:
    """Initialize the code repository, preserving existing repositories/worktrees."""
    require_git()
    if (config.root / ".git").exists():
        GitRepository(config)
        return
    GitRepository._execute(["git", "init", "--", str(config.root)], cwd=None)


def _repository(path: Path) -> GitRepository | None:
    try:
        return GitRepository(path)
    except GitError:
        return None


def _paths(config: HirakuConfig) -> tuple[str, str]:
    root = config.root.resolve()
    metadata = config.hiraku_dir.relative_to(root)
    if not metadata.parts or ".git" in metadata.parts:
        raise ValidationError("Git setup requires a separate Hiraku directory")
    return metadata.as_posix(), config.path.relative_to(config.root).as_posix()


def _ignore_pattern(value: str) -> str:
    return "/" + "".join("\\" + c if c in "\\*?[]#! " else c for c in value)


def _ignore_text(config: HirakuConfig, *, metadata: bool) -> str:
    path = config.root / ".gitignore"
    if path.is_symlink():
        raise ValidationError("Refusing to edit a symbolic-link .gitignore")
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if text.count(_START) != text.count(_END) or text.count(_START) > 1:
        raise ValidationError("Malformed Hiraku Git setup block in .gitignore")
    if _START in text:
        start = text.index(_START)
        end = text.index(_END) + len(_END)
        if end < start:
            raise ValidationError("Malformed Hiraku Git setup block in .gitignore")
        if text[end : end + 1] == "\n":
            end += 1
        text = text[:start] + text[end:]
    directory, marker = _paths(config)

    patterns = [_ignore_pattern(marker)]
    if metadata:
        patterns.insert(0, _ignore_pattern(directory) + "/")
    separator = "" if not text or text.endswith("\n") else "\n"
    return text + separator + "\n".join([_START, *patterns, _END, ""])


def _submodule(config: HirakuConfig, parent: GitRepository | None) -> bool:
    if parent is None:
        return False
    path = config.hiraku_dir.relative_to(parent.root).as_posix()
    entries = parent._git("ls-files", "--stage", "-z", "--", path).stdout
    return any(
        entry.startswith("160000 ") and entry.partition("\t")[2] == path
        for entry in entries.split("\0")
    )


def _check_untracked(config: HirakuConfig, parent: GitRepository | None) -> None:
    if parent is None:
        return
    paths = [config.hiraku_dir, config.path]
    for path in paths:
        relative = path.relative_to(parent.root).as_posix()
        if parent._git("ls-files", "--", relative).stdout:
            raise ValidationError(
                f"{relative} is already tracked by the code repository; "
                "migrate it explicitly before changing Hiraku Git setup"
            )


def status(config: HirakuConfig) -> dict[str, object]:
    """Inspect setup without changing files or requiring Git to be installed."""
    directory, marker = _paths(config)
    parent = _repository(config.root)
    metadata = (
        _repository(config.hiraku_dir)
        if (config.hiraku_dir / ".git").exists()
        else None
    )
    mode = "none"
    if _submodule(config, parent):
        mode = "submodule"
    elif metadata is not None:
        mode = "repo"
    elif parent is not None:
        ignored = parent._git(
            "check-ignore",
            "--",
            str(config.hiraku_dir) + "/",
            str(config.path),
            check=False,
        )
        if len(ignored.stdout.splitlines()) == 2:
            mode = "ignore"
    else:
        ignore = config.root / ".gitignore"
        if ignore.is_file() and not ignore.is_symlink():
            text = ignore.read_text(encoding="utf-8")
            if (
                _START in text
                and _ignore_pattern(directory) + "/\n" in text
                and _ignore_pattern(marker) + "\n" in text
            ):
                mode = "ignore"
    return {
        "git_available": shutil.which("git") is not None,
        "code_repository": parent.root if parent else None,
        "metadata_repository": metadata.root if metadata else None,
        "mode": mode,
    }


def setup(config: HirakuConfig, mode: str, *, url: str | None = None) -> None:
    """Configure metadata versioning; never commit, push, or untrack user files."""
    try:
        if mode not in {"ignore", "repo", "submodule"}:
            raise ValidationError(f"Unknown Git setup mode: {mode}")
        if mode != "submodule" and url is not None:
            raise ValidationError("--url is only supported for submodule setup")
        if mode == "submodule" and not url:
            raise ValidationError("Submodule setup requires --url PATH_OR_URL")
        _paths(config)
        parent = _repository(config.root)
        if _submodule(config, parent):
            if mode != "submodule":
                raise ValidationError(
                    "Hiraku is already a submodule; migrate it explicitly"
                )
            modules = parent.root / ".gitmodules"
            recorded = parent._git(
                "config",
                "--file",
                str(modules),
                "--get",
                "submodule.hiraku.url",
                check=False,
            ).stdout.strip()
            if recorded != _url(url):
                raise ValidationError("Existing submodule has a different URL")
            return
        _check_untracked(config, parent)
        ignore = _ignore_text(config, metadata=mode != "submodule")
        if mode == "repo":
            require_git()
            GitRepository._execute(
                ["git", "init", "--", str(config.hiraku_dir)], cwd=None
            )
        elif mode == "submodule":
            require_git()
            if parent is None:
                raise ValidationError(
                    "Initialize the code repository with git init first"
                )
            _setup_submodule(config, parent, _url(url), ignore)
            return
        atomic_write(config.root / ".gitignore", ignore)
    except (OSError, UnicodeError) as exc:
        raise GitError(f"Could not configure Hiraku Git setup: {exc}") from exc


def _url(url: str) -> str:
    if "://" in url or (":" in url and not Path(url).is_absolute()):
        return url
    return str(Path(url).expanduser().resolve())


def _setup_submodule(
    config: HirakuConfig, parent: GitRepository, url: str, ignore: str
) -> None:
    directory = config.hiraku_dir
    path = directory.relative_to(parent.root).as_posix()
    modules = parent.root / ".gitmodules"
    if modules.is_symlink():
        raise ValidationError("Refusing to edit a symbolic-link .gitmodules")
    if not directory.is_dir():
        raise ValidationError(f"Hiraku directory does not exist: {directory}")
    if not (directory / ".git").exists():
        raise ValidationError(
            "Hiraku metadata must already be a Git repository; "
            "run hiraku git setup repo first"
        )
    metadata = GitRepository(directory)
    if metadata.root != directory:
        raise ValidationError("Hiraku metadata must have its own Git repository")
    try:
        metadata.resolve("HEAD")
    except GitError as exc:
        raise ValidationError(
            f"Commit metadata first: git -C {directory} add .; "
            f'git -C {directory} commit -m "Initialize research history"'
        ) from exc
    if parent._git("status", "--porcelain", "--", ".gitmodules").stdout:
        raise ValidationError("Commit existing .gitmodules changes before setup")
    if parent._git(
        "config",
        "--file",
        str(modules),
        "--get",
        "submodule.hiraku.path",
        check=False,
    ).stdout:
        raise ValidationError("An existing submodule already uses the name hiraku")

    # Git's add command stages the registration. Save its touched parent files
    # so a failed registration cannot disturb an unrelated staged change.
    def git_path(name: str) -> Path:
        return Path(
            parent._git(
                "rev-parse", "--path-format=absolute", "--git-path", name
            ).stdout.strip()
        )

    snapshots = {
        file: file.read_bytes() if file.exists() else None
        for file in (
            modules,
            git_path("index"),
            git_path("config"),
            config.root / ".gitignore",
        )
    }
    try:
        parent._git("submodule", "add", "--force", "--name", "hiraku", "--", url, path)
        parent._git("config", "--file", str(modules), "submodule.hiraku.ignore", "all")
        parent._git("add", "--", ".gitmodules")
        atomic_write(config.root / ".gitignore", ignore)
    except Exception:
        for file, content in snapshots.items():
            if content is None:
                file.unlink(missing_ok=True)
            else:
                file.write_bytes(content)
        raise
