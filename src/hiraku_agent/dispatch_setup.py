"""Setting up and checking dispatchers (`setup-dispatch`, `check-dispatch`).

A dispatcher (`<hiraku_dir>/dispatch/NAME.sh`) decides where and how `hiraku launch`
runs an entrypoint. Its contract is small, and everything that makes a run
traceable depends on it:

- it is called as `NAME.sh [--commit REF] -- COMMAND...` from the project root, with
  `HIRAKU_RECORD_ID`, `HIRAKU_RECORD_FILE`, `HIRAKU_ROOT` and `HIRAKU_ENTRYPOINT` set;
- it runs COMMAND on exactly REF's code when `--commit` is given;
- it exports `HIRAKU_COMMIT` (the commit actually run) and `HIRAKU_GIT_STATE`
  (`clean`/`dirty`) to COMMAND;
- if it only *submits* the work (a scheduler), it prints the job id on the last line of
  stdout and records it with `hiraku-agent mark-running`.

`check_dispatch` verifies that contract by sending a small probe command through the
dispatcher: no LLM, no record is touched. `run_setup_dispatch` asks the agent to pick a
template (`worktree.sh`, `slurm.sh`) and fill in site settings from what it finds (tools
on PATH, existing `.sbatch` files, cluster configs, the instruction), or, for a backend
with no template, to write a dispatcher that follows the contract. It never changes the
project's `default_dispatch`; `check-dispatch NAME --set-default` does, and only after
the check passes.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass, field
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any

from hiraku.config import HirakuConfig

from .actions import apply_overrides
from .defaults import load_defaults
from .engine import (
    AgentRun,
    Hooks,
    attach_context,
    build_prompt,
    extract_json,
    gather_context,
    invoke,
    with_repos,
)
from .errors import AgentError
from .launches import launch_dir, mark_done

CHECK_RECORD_ID = "dispatch-check"
TEMPLATES = ("launch.sh", "worktree.sh", "slurm.sh")
# Tools whose presence says which backend a machine can launch on.
_TOOLS = (
    "sbatch",
    "squeue",
    "qsub",
    "bsub",
    "condor_submit",
    "ray",
    "kubectl",
    "sky",
    "dstack",
    "modal",
    "docker",
    "apptainer",
    "singularity",
)
# File patterns that reveal how a project already launches work.
_SIGNALS = (
    ("slurm", re.compile(r"^#SBATCH", re.M)),
    ("pbs", re.compile(r"^#PBS", re.M)),
    ("lsf", re.compile(r"^#BSUB", re.M)),
    ("ray", re.compile(r"^\s*(head_node_type|cluster_name):|ray job submit", re.M)),
    ("kubernetes", re.compile(r"^kind:\s*(Job|PyTorchJob|RayJob)\b", re.M)),
    ("skypilot", re.compile(r"^(resources|run):", re.M)),
    ("hydra-launcher", re.compile(r"hydra/launcher", re.M)),
)
_SUFFIXES = {".sbatch", ".slurm", ".sh", ".pbs", ".yaml", ".yml", ".job"}
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".hiraku"}


# ----------------------------------------------------------------------- templates


def template_text(name: str) -> str:
    """A dispatcher template shipped with `hiraku`."""
    resource = files("hiraku.resources") / "dispatch" / name
    with as_file(resource) as path:
        return Path(path).read_text(encoding="utf-8")


def _write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


# ----------------------------------------------------------------------- detection


def _scan(root: Path, *, limit: int = 4000) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    seen = 0
    for directory, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in names:
            path = Path(directory) / name
            if path.suffix not in _SUFFIXES and name != "Dockerfile":
                continue
            seen += 1
            if seen > limit:
                return found
            if name == "Dockerfile":
                found.append(("container", path))
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")[:20000]
            except OSError:
                continue
            for label, pattern in _SIGNALS:
                if pattern.search(text):
                    found.append((label, path))
                    break
    return found


def detect(
    config: HirakuConfig, extra_roots: list[str] | None = None
) -> dict[str, Any]:
    """What this machine and these repositories say about how work is launched."""
    roots = [config.root]
    for raw in [*load_defaults(config).repos, *(extra_roots or [])]:
        path = Path(raw).expanduser()
        path = path if path.is_absolute() else config.root / path
        if path.is_dir() and path.resolve() not in {r.resolve() for r in roots}:
            roots.append(path)
    files_found: list[dict[str, Any]] = []
    for root in roots:
        for label, path in _scan(root):
            files_found.append({"kind": label, "path": str(path)})
    excerpts: dict[str, str] = {}
    for entry in [f for f in files_found if f["kind"] != "container"][:6]:
        try:
            lines = Path(entry["path"]).read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        excerpts[entry["path"]] = "\n".join(lines[:40])
    dispatch_dir = config.dispatch_dir
    return {
        "tools_on_path": [tool for tool in _TOOLS if shutil.which(tool)],
        "files": files_found[:40],
        "excerpts": excerpts,
        "existing_dispatchers": sorted(p.name for p in dispatch_dir.glob("*.sh"))
        if dispatch_dir.is_dir()
        else [],
        "default_dispatch": config.default_dispatch,
        "templates": list(TEMPLATES),
        "searched": [str(root) for root in roots],
    }


# ------------------------------------------------------------------------- setup


def run_setup_dispatch(
    instruction: str = "",
    *,
    name: str | None = None,
    force: bool = False,
    command: list[str] | None = None,
    dry_run: bool = False,
    apply: bool = True,
    repo_paths: list[str] | None = None,
    context_paths: list[str] | None = None,
    context_bytes: int | None = None,
    context_id: str | None = None,
    action: str | None = None,
    unset_env: list[str] | None = None,
    hooks: Hooks | None = None,
    timeout: int | None = None,
    config: HirakuConfig | None = None,
) -> AgentRun:
    """Have the agent choose and configure a dispatcher for this project.

    Writes `<hiraku_dir>/dispatch/NAME` (a template copy, or a script the agent wrote
    for a backend with no template) and, for Slurm, `slurm.header` with the `#SBATCH`
    lines it settled on. An existing file is never overwritten without `force`. The
    project's `default_dispatch` is left alone: run `check-dispatch NAME --set-default`.
    """
    selected = config or HirakuConfig.discover()
    command, timeout, context_paths = apply_overrides(
        action,
        context_id,
        entity=None,
        command=command,
        timeout=timeout,
        context_paths=context_paths,
        config=selected,
    )
    bundle = gather_context(selected.root, context_paths, max_bytes=context_bytes)
    state = {"detected": detect(selected, repo_paths), "requested_name": name}
    command = with_repos(command, repo_paths)
    prompt = build_prompt(
        "setup_dispatch",
        attach_context(state, bundle),
        instruction=instruction,
        root=selected.root,
        repos=repo_paths,
    )
    result = AgentRun(workflow="setup_dispatch", prompt=prompt, context=bundle)
    if dry_run:
        return result

    result.raw, result.log, result.seconds = invoke(
        selected,
        "setup_dispatch",
        "project",
        prompt,
        bundle,
        command=command,
        timeout=timeout,
        hooks=hooks,
        unset_env=unset_env,
    )
    data = extract_json(result.raw, keys=("template", "backend"))
    result.data = data
    if not apply:
        return result

    template = str(data.get("template") or "").strip() or None
    target_name = _dispatcher_name(name or data.get("name") or template)
    target = selected.dispatch_dir / target_name
    if template is not None and template not in TEMPLATES:
        raise AgentError(
            f"Unknown template {template!r}; one of {', '.join(TEMPLATES)}."
        )
    script = None if template else str(data.get("script") or "").strip()
    if not template and not script:
        raise AgentError(
            "The agent chose neither a template nor wrote a script. "
            "Nothing was written."
        )
    if script is not None and not script.startswith("#!"):
        raise AgentError("The agent's script has no #! line. Nothing was written.")

    planned: list[tuple[Path, str, bool]] = [
        (target, template_text(template) if template else f"{script}\n", True)
    ]
    header = _sbatch_lines(data.get("sbatch_lines"))
    if template == "slurm.sh":
        planned.append((selected.dispatch_dir / "slurm.header", header, False))
    clashes = [
        path
        for path, text, _ in planned
        if path.exists()
        and path.read_text(encoding="utf-8") != text
        and not _unconfigured_header(path)
    ]
    if clashes and not force:
        raise AgentError(
            "Would overwrite "
            + ", ".join(str(p) for p in clashes)
            + ". Re-run with --force to replace them. Nothing was written."
        )
    for path, text, executable in planned:
        if path.exists() and path.read_text(encoding="utf-8") == text:
            continue
        if executable:
            _write_executable(path, text)
        else:
            path.write_text(text, encoding="utf-8")
        result.written.append(path)
    data["next"] = (
        f"review {target}, then run `hiraku-agent check-dispatch {target_name}` "
        f"(add --set-default to make it the project default once it passes)"
    )
    return result


def _unconfigured_header(path: Path) -> bool:
    """`slurm.header` as `hiraku init` installs it: comments only, nothing to lose."""
    if path.name != "slurm.header":
        return False
    text = path.read_text(encoding="utf-8")
    return not re.search(r"^#SBATCH", text, re.M)


def _dispatcher_name(raw: Any) -> str:
    name = str(raw or "").strip() or "custom.sh"
    if not name.endswith(".sh"):
        name += ".sh"
    if "/" in name or name.startswith("."):
        raise AgentError(f"Invalid dispatcher name {name!r}.")
    return name


def _sbatch_lines(raw: Any) -> str:
    lines = raw if isinstance(raw, list) else str(raw or "").splitlines()
    kept = [
        str(line).strip() for line in lines if str(line).strip().startswith("#SBATCH")
    ]
    head = (
        "# Site settings for slurm.sh, written by `hiraku-agent setup-dispatch`.\n"
        "# Every line starting with #SBATCH is copied into the job script.\n"
    )
    return head + "\n".join(kept) + ("\n" if kept else "")


# ------------------------------------------------------------------------- check


@dataclass
class CheckResult:
    dispatcher: str
    status: str = "fail"  # pass | fail | pending
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    job_id: str | None = None
    output: str = ""

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append((name, ok, detail))


_PROBE = (
    'printf "%s\\n%s\\n%s\\n%s\\n" "${HIRAKU_COMMIT-}" "${HIRAKU_GIT_STATE-}" '
    '"$(git rev-parse HEAD 2>/dev/null || echo unknown)" "${HIRAKU_RECORD_ID-}" '
    '> "$HIRAKU_CHECK_RESULT"'
)


def check_dispatch(
    name: str,
    *,
    wait: float = 120.0,
    timeout: float = 300.0,
    config: HirakuConfig | None = None,
) -> CheckResult:
    """Send a probe through dispatcher `name` and check the contract.

    The probe asks for an *older* commit than the current checkout when there is one,
    so a dispatcher that ignores `--commit` (like `launch.sh`) is caught. For a
    scheduler, waits up to `wait` seconds for the job to run the probe.
    """
    selected = config or HirakuConfig.discover()
    script = selected.dispatch_dir / _dispatcher_name(name)
    result = CheckResult(dispatcher=script.name)
    if not script.is_file():
        raise AgentError(f"No dispatcher {script}.")
    if not os.access(script, os.X_OK):
        raise AgentError(f"{script} is not executable (chmod +x it).")

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(selected.root), *args],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()

    head = git("rev-parse", "HEAD")
    if not head:
        raise AgentError("The project has no commit yet; commit once, then check.")
    older = git("rev-parse", "--verify", "-q", "HEAD~1^{commit}")
    requested = older or head

    probe_dir = launch_dir(selected).parent / "hiraku-dispatch-check"
    probe_dir.mkdir(parents=True, exist_ok=True)
    result_file = probe_dir / f"{secrets.token_hex(6)}.txt"
    env = {
        **os.environ,
        "HIRAKU_RECORD_ID": CHECK_RECORD_ID,
        "HIRAKU_RECORD_FILE": "",
        "HIRAKU_ROOT": str(selected.root.resolve()),
        "HIRAKU_ENTRYPOINT": "dispatch-check",
        "HIRAKU_COMMIT": "",
        "HIRAKU_GIT_STATE": "unavailable",
        "HIRAKU_CHECK_RESULT": str(result_file),
    }
    try:
        completed = subprocess.run(
            [str(script), "--commit", requested, "--", "/bin/sh", "-c", _PROBE],
            cwd=selected.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        result.add("returns", False, f"did not return within {timeout:.0f}s")
        return result
    result.output = (completed.stdout + completed.stderr).strip()
    result.add(
        "exits 0",
        completed.returncode == 0,
        f"exit status {completed.returncode}" if completed.returncode else "",
    )
    if completed.returncode != 0:
        return result

    last = (completed.stdout.strip().splitlines() or [""])[-1].strip()
    if re.fullmatch(r"\d+(_\d+)?(;\S+)?|[A-Za-z][\w.-]*-\w+", last):
        result.job_id = last.split(";")[0]
    deadline = time.monotonic() + (wait if result.job_id else 0)
    while not result_file.exists() and time.monotonic() < deadline:
        time.sleep(2)
    if result.job_id:
        markers = launch_dir(selected) / f"{CHECK_RECORD_ID}.json"
        result.add(
            "records the job (mark-running)",
            markers.exists(),
            "" if markers.exists() else "no running marker; is hiraku-agent on PATH?",
        )
        mark_done(CHECK_RECORD_ID, config=selected)
    if not result_file.exists():
        if result.job_id:
            result.status = "pending"
            result.add(
                "job ran the probe",
                False,
                f"job {result.job_id} has not run within {wait:.0f}s; re-run with a "
                f"longer --wait once the queue moves",
            )
        else:
            result.add("ran the command", False, "the probe left no result")
        return result

    reported, state, actual, record = (
        result_file.read_text(encoding="utf-8").splitlines() + ["", "", "", ""]
    )[:4]
    result_file.unlink(missing_ok=True)
    result.add(
        "runs the requested commit",
        actual in {requested, "unknown"},
        f"asked for {requested[:12]}, ran {actual[:12]}"
        + (" (it ignores --commit)" if actual == head and requested != head else "")
        if actual not in {requested, "unknown"}
        else ("could not verify: no git inside the job" if actual == "unknown" else ""),
    )
    result.add(
        "exports HIRAKU_COMMIT",
        reported == (actual if actual != "unknown" else requested),
        f"reported {reported[:12] or 'nothing'}",
    )
    result.add(
        "exports HIRAKU_GIT_STATE",
        state in {"clean", "dirty"},
        f"reported {state or 'nothing'}",
    )
    result.add("passes HIRAKU_RECORD_ID", record == CHECK_RECORD_ID, record)
    if all(ok for _, ok, _ in result.checks):
        result.status = "pass"
    return result


def set_default_dispatch(name: str, *, config: HirakuConfig) -> None:
    config.default_dispatch = _dispatcher_name(name)
    config.save()


def describe_check(result: CheckResult) -> str:
    lines = [f"{result.dispatcher}: {result.status}"]
    for name, ok, detail in result.checks:
        lines.append(
            f"  [{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else "")
        )
    if result.job_id:
        lines.append(f"  job id: {result.job_id}")
    if result.status != "pass" and result.output:
        lines.append("  dispatcher output:")
        lines += [f"    {line}" for line in result.output.splitlines()[-15:]]
    return "\n".join(lines)


def to_json(result: CheckResult) -> str:
    return json.dumps(
        {
            "dispatcher": result.dispatcher,
            "status": result.status,
            "job_id": result.job_id,
            "checks": [
                {"name": n, "ok": ok, "detail": d} for n, ok, d in result.checks
            ],
        },
        indent=2,
    )
