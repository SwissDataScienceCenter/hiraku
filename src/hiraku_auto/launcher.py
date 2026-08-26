"""Launching an experiment entrypoint and recording the run.

Two forms, and the difference is what the promotion gate reads:

* working tree — runs the current checkout including uncommitted changes. Fast, for debugging.
  Recorded as `run_kind: worktree`, `reproducible: false`.
* `--commit <ref>` — checks the commit out into a temporary detached worktree and runs there,
  leaving the active checkout alone. `run_kind: commit`, `reproducible: true`.

The launcher writes the run row itself, so provenance columns are consistent across projects
rather than reimplemented by each one. The child reports its metrics by writing a JSON object to
`$HIRAKU_METRICS_PATH`; those keys are merged into the row.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import gitops, tracking, yamlio
from .config import Project
from .errors import HirakuError
from .records import load_experiment
from .runs import append_run

LOCKFILES = ("uv.lock", "poetry.lock", "requirements.txt", "environment.yml", "Pipfile.lock")

# Hashing a multi-gigabyte image on every run would be wasteful, so digests are cached by
# (path, size, mtime). Delete the file to force a rehash.
DIGEST_CACHE = "image-digests.json"


@dataclass
class LaunchResult:
    run_id: str
    exit_code: int
    row: dict[str, Any]
    reproducible: bool


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def lockfile_digest(*roots: Path) -> tuple[str, str]:
    """(label, sha256 prefix) of the first lockfile found, searching `roots` in order.

    Identity of the dependency set without storing it: two runs sharing the hash had the same
    dependencies. The experiment's own directory is searched before the project root, because an
    experiment with its own environment has its own lockfile — recording the project's would name
    the wrong file, which is worse than recording nothing.
    """
    for root in roots:
        for name in LOCKFILES:
            candidate = root / name
            if candidate.is_file():
                digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
                label = name if root == roots[-1] else f"{root.name}/{name}"
                return label, digest[:16]
    return "", ""


def detect_accelerator() -> str:
    """One short string. Best effort — absent hardware is recorded as empty, never guessed."""
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            out = subprocess.run(
                [smi, "--query-gpu=name,memory.total", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            lines = [line.strip() for line in out.stdout.splitlines() if line.strip()]
            if lines:
                name, _, memory = lines[0].partition(",")
                return f"{len(lines)}x {name.strip()} {memory.strip().replace(' MiB', 'MB')}"
        except (OSError, subprocess.SubprocessError):
            pass
    if sys.platform == "darwin":
        return f"apple-{platform.machine()}"
    return ""


def image_digest(project: Any, container: dict[str, Any]) -> tuple[str, str]:
    """(image, digest) for a containerised run.

    For a container, the image IS the environment: `lockfile_sha256` and `interpreter` describe
    the launcher's side of the bind mount, not what the code ran against. Without the image
    identity a container run's `reproducible: true` would be claiming more than it knows.

    An explicit `digest_cmd` wins. Otherwise a local image file is hashed, cached by
    (path, size, mtime) so a large image is hashed once. A registry reference with no
    `digest_cmd` records the reference alone — pin it by tag digest or set `digest_cmd`.
    """
    image = str(container.get("image") or "")
    if not image:
        return "", ""

    digest_cmd = container.get("digest_cmd")
    if digest_cmd:
        try:
            out = subprocess.run(
                list(digest_cmd), capture_output=True, text=True, timeout=120, cwd=str(project.root)
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise HirakuError(f"container.digest_cmd failed: {exc}") from exc
        if out.returncode != 0:
            raise HirakuError(
                f"container.digest_cmd exited {out.returncode}: {(out.stderr or '')[:200]}"
            )
        return image, out.stdout.strip().splitlines()[0] if out.stdout.strip() else ""

    candidate = Path(image)
    if not candidate.is_absolute():
        candidate = project.root / image
    if not candidate.is_file():
        return image, ""

    stat = candidate.stat()
    key = f"{candidate.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
    cache_path = project.table_path.parent / DIGEST_CACHE
    cache: dict[str, str] = {}
    if cache_path.is_file():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8")) or {}
        except (OSError, json.JSONDecodeError):
            cache = {}
    if key in cache:
        return image, cache[key]

    digest = hashlib.sha256()
    with candidate.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    value = f"sha256:{digest.hexdigest()}"
    cache[key] = value
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except OSError:
        pass
    return image, value


def load_entrypoints(directory: Path) -> dict[str, Any]:
    path = directory / "launch.yml"
    if not path.is_file():
        raise HirakuError(f"No launch.yml in {directory}.")
    data = yamlio.load_file(path)
    entrypoints = data.get("entrypoints")
    if not isinstance(entrypoints, dict) or not entrypoints:
        raise HirakuError(f"{path} declares no entrypoints.")
    return entrypoints


def resolve_entrypoint(
    entrypoints: dict[str, Any], name: str
) -> tuple[list[str], str, dict[str, str], str, dict[str, Any]]:
    spec = entrypoints.get(name)
    if spec is None:
        raise HirakuError(
            f"Entrypoint '{name}' not found. Available: {', '.join(sorted(entrypoints))}"
        )
    if not isinstance(spec, dict):
        raise HirakuError(f"Entrypoint '{name}' must be a mapping.")
    command = spec.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(c, str) for c in command):
        raise HirakuError(f"Entrypoint '{name}' needs a non-empty argv list in 'command'.")
    cwd = str(spec.get("cwd") or ".")
    if Path(cwd).is_absolute() or ".." in Path(cwd).parts:
        raise HirakuError(f"Entrypoint '{name}' has a cwd outside the project: {cwd}")
    env = spec.get("env") or {}
    if not isinstance(env, dict) or any(not isinstance(v, (str, int, float)) for v in env.values()):
        raise HirakuError(f"Entrypoint '{name}' env must be a flat string map.")
    clean_env = {str(k): str(v) for k, v in env.items()}
    for key in clean_env:
        if key.startswith("HIRAKU_"):
            raise HirakuError(f"Entrypoint '{name}' may not set reserved variable {key}.")
    venv = str(spec.get("venv") or "")
    if venv and (Path(venv).is_absolute() or ".." in Path(venv).parts):
        raise HirakuError(f"Entrypoint '{name}' has a venv outside the project: {venv}")

    container = spec.get("container") or {}
    if container and not isinstance(container, dict):
        raise HirakuError(f"Entrypoint '{name}': 'container' must be a mapping.")
    if container:
        if not isinstance(container.get("image"), str) or not container["image"]:
            raise HirakuError(f"Entrypoint '{name}': container needs a string 'image'.")
        digest_cmd = container.get("digest_cmd")
        if digest_cmd is not None and (
            not isinstance(digest_cmd, list) or not all(isinstance(c, str) for c in digest_cmd)
        ):
            raise HirakuError(f"Entrypoint '{name}': container.digest_cmd must be an argv list.")
    return command, cwd, clean_env, venv, dict(container)


def _goal_of(project: Project, experiment_id: str) -> str | None:
    """The goal an experiment belongs to, inherited through its parent chain.

    Read lazily and defensively: mirroring must never be the reason a launch fails, so a broken
    tree means "no goal name" rather than an exception on the way to recording a finished run.
    """
    try:
        from .tree import build

        forest = build(project)
        node = forest.nodes.get(experiment_id)
        return node.goal if node else None
    except Exception:
        return None


def launch(
    project: Project,
    experiment_id: str,
    entrypoint: str,
    *,
    commit: str | None = None,
    seed: Any = None,
    dry_run: bool = False,
    extra_env: dict[str, str] | None = None,
) -> LaunchResult:
    record = load_experiment(project, experiment_id)
    run_id = f"r-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"

    reproducible = commit is not None
    temp_root: Path | None = None
    source_root = project.root

    if reproducible:
        sha = gitops.resolve_ref(project.root, str(commit))
        temp_root = Path(tempfile.mkdtemp(prefix="hiraku-run-"))
        source_root = gitops.add_temp_worktree(project.root, sha, temp_root / "tree")
        recorded_commit = sha
    else:
        recorded_commit = gitops.head_commit(project.root) if gitops.is_repo(project.root) else ""

    try:
        exp_dir = source_root / project.rel(record.path.parent)
        entrypoints = load_entrypoints(exp_dir)
        command, cwd, manifest_env, venv, container = resolve_entrypoint(
            entrypoints, entrypoint
        )
        workdir = (exp_dir / cwd).resolve()

        metrics_path = Path(tempfile.mkstemp(prefix="hiraku-metrics-", suffix=".json")[1])
        metrics_path.write_text("{}", encoding="utf-8")

        lockfile_name, lockfile_hash = lockfile_digest(
            project.experiments_dir / experiment_id, source_root
        )
        env = os.environ.copy()
        env.update(manifest_env)
        env.update(extra_env or {})

        # A per-experiment environment: prepend its bin/ so `python` in the command resolves
        # there. This is what lets two backends with conflicting dependencies coexist, without
        # the launcher having to know anything about how the environment was built.
        #
        # Resolved against the LIVE checkout, never the temporary commit worktree. A virtual
        # environment is machine state, not commit state: its paths are absolute, so a copy at a
        # new path is broken, and it is not something you commit. `--commit` therefore isolates
        # code, not dependencies — which dependency set ran is recorded by lockfile_sha256.
        venv_dir = ""
        if venv:
            live_exp_dir = project.experiments_dir / experiment_id
            candidate = (
                (live_exp_dir / venv) if (live_exp_dir / venv).exists() else (project.root / venv)
            )
            if not candidate.is_dir():
                raise HirakuError(
                    f"Entrypoint '{entrypoint}' names venv '{venv}', which does not exist "
                    f"(looked in {live_exp_dir} and {project.root}). Create it first, e.g. "
                    f"`cd {project.rel(live_exp_dir)} && uv venv {venv}`."
                )
            bindir = candidate / ("Scripts" if os.name == "nt" else "bin")
            venv_dir = str(candidate)
            env["VIRTUAL_ENV"] = venv_dir
            env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
            env.pop("PYTHONHOME", None)
        env.update(
            {
                "HIRAKU_EXPERIMENT_ID": experiment_id,
                "HIRAKU_GIT_COMMIT": recorded_commit,
                "HIRAKU_RUN_ID": run_id,
                "HIRAKU_RUN_KIND": "commit" if reproducible else "worktree",
                "HIRAKU_REPRODUCIBLE": "true" if reproducible else "false",
                "HIRAKU_ENTRYPOINT": entrypoint,
                "HIRAKU_TRACKING_BACKEND": "table",
                "HIRAKU_TABLE_PATH": str(project.table_path),
                "HIRAKU_METRICS_PATH": str(metrics_path),
                "HIRAKU_PROJECT_ROOT": str(project.root),
            }
        )
        if seed is not None:
            env["HIRAKU_SEED"] = str(seed)

        container_image, container_digest = image_digest(project, container)
        if container and not container_digest:
            print(
                f"warning: no digest for image '{container_image}'. The run will record the "
                f"reference only, so the environment is not pinned. Set container.digest_cmd.",
                file=sys.stderr,
            )

        if dry_run:
            print(f"would run: {' '.join(command)}")
            print(f"       in: {workdir}")
            print(f"  run_kind: {'commit' if reproducible else 'worktree'}")
            print(f"    commit: {recorded_commit or '(no repository)'}")
            print(f"      venv: {venv or '(inherits the caller environment)'}")
            if container:
                print(f"     image: {container_image}")
                print(f"    digest: {container_digest or '(none — not pinned)'}")
            print(f" run table: {project.table_path}")
            return LaunchResult(run_id=run_id, exit_code=0, row={}, reproducible=reproducible)

        started = time.monotonic()
        try:
            completed = subprocess.run(command, cwd=str(workdir), env=env)
        except FileNotFoundError as exc:
            raise HirakuError(f"Command not found: {command[0]}") from exc
        duration = round(time.monotonic() - started, 3)

        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8") or "{}")
            if not isinstance(metrics, dict):
                metrics = {}
        except (OSError, json.JSONDecodeError):
            metrics = {}
        metrics_path.unlink(missing_ok=True)

        row: dict[str, Any] = {
            "run_id": run_id,
            "experiment_id": experiment_id,
            "commit": recorded_commit,
            "run_kind": "commit" if reproducible else "worktree",
            "reproducible": "true" if reproducible else "false",
            "launched_at": _utc_now(),
            "entrypoint": entrypoint,
            "exit_code": completed.returncode,
            "duration_s": duration,
            "seed": "" if seed is None else seed,
            "python_version": platform.python_version(),
            "lockfile_sha256": f"{lockfile_name}:{lockfile_hash}" if lockfile_hash else "",
            "interpreter": shutil.which(command[0], path=env.get("PATH")) or command[0],
            "venv": venv_dir,
            "container_image": container_image,
            "container_digest": container_digest,
            "platform": f"{platform.system()}-{platform.release()}-{platform.machine()}",
            "accelerator": detect_accelerator(),
        }
        for key, value in metrics.items():
            if key not in row:
                row[key] = value

        # Mirror before appending, so the mlflow run id lands in the same row. A mirror failure is
        # a warning: the row is the record, the mirror is a convenience.
        resolved = tracking.resolve(
            project, experiment_id, goal_id=_goal_of(project, experiment_id)
        )
        extra, warning = tracking.log_run(project, resolved, row)
        row.update(extra)
        if warning:
            print(f"warning: {warning}", file=sys.stderr)

        append_run(project.table_path, row)
        return LaunchResult(
            run_id=run_id,
            exit_code=completed.returncode,
            row=row,
            reproducible=reproducible,
        )
    finally:
        if temp_root is not None:
            try:
                gitops.worktree_remove(project.root, temp_root / "tree", force=True)
            except HirakuError:
                pass
            shutil.rmtree(temp_root, ignore_errors=True)
