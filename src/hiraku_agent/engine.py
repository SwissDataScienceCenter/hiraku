"""Calling an LLM agent CLI (Claude Code, by default) and getting one JSON object back.

The contract, in both directions, is JSON:

    task + nodes -> prompt -> `claude -p` ->
    one JSON object -> workflows.py writes nodes

The agent decides; `workflows.py` writes. That split is the point: it keeps file writes
deterministic and reviewable, keeps the agent from wandering outside its one task, and
means a bad suggestion is a diff you can revert rather than an edit you have to find.

This module is the generic half — resolving the command, building the prompt, running
it, parsing the reply, logging the call — and knows nothing about nodes, tasks, or what
`design`/`discover`/`triage`/`analyze` mean. `workflows.py` is the half that does.

Ported from the pre-`hiraku` `hiraku_auto`'s `agent.py`, which called the same way
against a hand-rolled record tree. Two things did NOT come along, on purpose, and both
are scoped down rather than silently dropped:

- No `agent.command` / `agent.timeout` project-config layer (the old
  `research/config.md` a researcher could edit). `hiraku_agent` has no such file yet,
  so precedence here is just `--agent-cmd` (or the `command=` argument), then
  `HIRAKU_AGENT_CMD`, then the default — one layer short of the old three. Add the
  project-config layer back by extending `agent_command()` once `hiraku_agent` has a
  natural place to put it (`HirakuConfig` itself allows extra fields —
  `config.model_extra.get("agent", {})` — the moment there is a `hiraku config set` or
  equivalent to write one).
- No automatic project-file context bundle (the old version scanned the project's own
  README/docs/memory file by default for `draft`/`design`/`discover`).
  `gather_context()` here only reads files the caller names explicitly with
  `context_paths` — real, and still useful, but it does not go looking on its own.
  Auto-discovery needs a convention for where a `hiraku` project keeps its prose
  (there is no `research/config.md` here), which is a project-layout decision, not one
  this module should make unilaterally.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hiraku.config import HirakuConfig

from .errors import AgentError, AgentTimeout

DEFAULT_AGENT_CMD = "claude -p"
DEFAULT_TIMEOUT = 900

# `design`/`discover`/`triage`/`analyze`/`draft`/`update` only ever need to read and
# return one JSON object, so the plain default above is deliberately narrow —
# `claude -p` with no `--allowedTools` denies anything it was not told about up front
# (see `KNOWN_FAILURES`'s "requested permissions to use" entry), which is exactly the
# least privilege those six workflows need. `implement` (`workflows.run_implement`) is
# the one workflow whose whole job is to edit real files and make a real commit, so it
# needs its own, separately-named default and override variable rather than quietly
# widening what `HIRAKU_AGENT_CMD`/`DEFAULT_AGENT_CMD` grant everything else.
DEFAULT_IMPLEMENT_AGENT_CMD = "claude -p --allowedTools Read,Write,Edit,Glob,Grep,Bash"
IMPLEMENT_AGENT_CMD_ENV_VAR = "HIRAKU_IMPLEMENT_AGENT_CMD"

WORKFLOWS = (
    "design",
    "discover",
    "triage",
    "analyze",
    "draft",
    "implement",
    "update",
    "revise",
    "analyze_task",
)

# Failures whose message names a symptom but not the fix. Matched against the whole
# output, so a warning on stderr and the real error on stdout both count.
KNOWN_FAILURES: tuple[tuple[str, str], ...] = (
    (
        "connectors are disabled because ANTHROPIC_API_KEY",
        "ANTHROPIC_API_KEY is set in your environment, so `claude` used it instead\n"
        "of your claude.ai login — and that key then failed. Two consequences beyond\n"
        "this error: your organisation's connectors do not load, and the calls bill\n"
        "to the API account rather than your subscription.\n\n"
        "  env -u ANTHROPIC_API_KEY hiraku-agent ...     # this one command\n"
        "  # or find where it is exported and remove it:\n"
        "  grep -rn ANTHROPIC_API_KEY ~/.zshrc ~/.zprofile \\\n"
        "    ~/.bashrc ~/.profile 2>/dev/null\n",
    ),
    (
        "Invalid API key",
        "The API key `claude` used was rejected. If you meant to use your claude.ai\n"
        "login, unset ANTHROPIC_API_KEY: `env -u ANTHROPIC_API_KEY hiraku-agent ...`",
    ),
    (
        "requested permissions to use",
        "`claude -p` runs non-interactively, so a tool it was not given up front is\n"
        "requested and denied. Pass it explicitly: `--agent-cmd 'claude -p\n"
        "--allowedTools WebSearch,WebFetch,Read,Glob,Grep'`.",
    ),
    (
        "Please run `claude login`",
        "`claude` is not logged in. Run `claude` once interactively and sign in,\n"
        "then retry.",
    ),
    (
        "Credit balance is too low",
        "The API account has no credit. If you meant to use your subscription\n"
        "instead, unset ANTHROPIC_API_KEY: `env -u ANTHROPIC_API_KEY hiraku-agent ...`",
    ),
)

WEB_TOOLS = ("websearch", "webfetch")
WEB_DENIED_MARKERS = (
    "permission was requested and denied",
    "permission requested and denied",
    "permission to use websearch",
    "permission to use webfetch",
    "websearch is not permitted",
    "webfetch is not permitted",
    "do not have permission to search",
    "without web access",
    "no web access",
)
UNVERIFIED_NOTE = (
    "**[UNVERIFIED: the agent had no web access on this call — WebSearch/WebFetch "
    "were not permitted — so every citation below is recalled from model knowledge "
    "and has not been checked against a primary source. Verify each one, or re-run "
    "`hiraku-agent discover` with `--agent-cmd 'claude -p --allowedTools "
    "WebSearch,WebFetch,Read,Glob,Grep'`.]**"
)


def web_allowed(command: list[str]) -> bool:
    """Can this command actually reach the web, or will `claude` ask and be refused?

    `claude -p` is non-interactive, so a tool it has not been given up front is
    requested and denied — silently, as far as this tool is concerned: the call
    succeeds and returns citations that were recalled rather than read. Only a
    command that names the web tools can check them.
    """
    if not command:
        return False
    if Path(command[0]).name not in {"claude", "claude.exe"}:
        return True  # some other agent: not ours to second-guess
    text = " ".join(command).lower()
    if "--dangerously-skip-permissions" in text or "bypasspermissions" in text:
        return True
    return any(tool in text for tool in WEB_TOOLS)


FILE_TOOLS = ("read", "grep", "glob")
UNVERIFIED_FILES_NOTE = (
    "**[UNVERIFIED: the agent had no file access on this call — no Read/Glob/Grep tool "
    "and no `--context` file — so nothing below was read from this project's files, "
    "logs or code; it is recalled or inferred. Re-run with `--repo PATH` or "
    "`--context FILE`.]**"
)


def files_readable(command: list[str] | None, bundle: Bundle | None) -> bool:
    """Could the agent actually read this project's files on this call?

    True when the caller handed it files (`--context`), or its `claude` command names a
    read tool (`--allowedTools Read,...`, which is what `--repo` adds) or skips
    permissions. A plain `claude -p` can read nothing it was not given, and an
    investigation written without that access would be recalled knowledge presented as
    reading — the same trap `web_allowed` guards against for citations.
    """
    if bundle:
        return True
    argv = command or agent_command()
    if Path(argv[0]).name not in {"claude", "claude.exe"}:
        return True  # some other agent: not ours to second-guess
    text = " ".join(argv).lower()
    if "--dangerously-skip-permissions" in text or "bypasspermissions" in text:
        return True
    return any(tool in text for tool in FILE_TOOLS)


def web_was_denied(reply: str) -> bool:
    """Did the agent say, in its own words, that it could not search?"""
    low = reply.lower()
    return any(marker in low for marker in WEB_DENIED_MARKERS)


def diagnose(output: str) -> str:
    """The actionable half of an agent failure, when the message alone doesn't say
    what to do."""
    for needle, advice in KNOWN_FAILURES:
        if needle.lower() in output.lower():
            return advice
    return ""


# ----------------------------------------------------------------------- context bundle


@dataclass
class Bundle:
    """Explicitly named project files, read verbatim and offered to the agent as
    background.

    See this module's docstring for why there is no automatic discovery: only
    files the caller names in `context_paths` end up here.
    """

    files: dict[str, str] = field(default_factory=dict)
    truncated: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.files)

    def to_state(self) -> list[dict[str, str]]:
        return [{"path": path, "excerpt": text} for path, text in self.files.items()]

    def summary(self) -> dict[str, Any]:
        return {
            "files": list(self.files),
            "bytes": sum(len(text) for text in self.files.values()),
            "truncated": list(self.truncated),
        }


def gather_context(
    root: Path,
    paths: list[str] | None,
    *,
    max_bytes: int | None = None,
) -> Bundle:
    """Read each of `paths` (relative to `root`, or absolute) into a `Bundle`.

    A missing file is skipped rather than raised — `--context` is meant to be handed a
    handful of paths casually, and one typo should not sink the whole call. `max_bytes`
    caps each file individually (default 20_000 characters), not the total, so one large
    file cannot silently crowd the others out.
    """
    cap = max_bytes if max_bytes and max_bytes > 0 else 20_000
    bundle = Bundle()
    for raw in paths or []:
        candidate = Path(raw)
        resolved = candidate if candidate.is_absolute() else root / candidate
        if not resolved.is_file():
            continue
        text = resolved.read_text(encoding="utf-8", errors="replace")
        label = raw
        if len(text) > cap:
            text = text[:cap]
            bundle.truncated.append(label)
        bundle.files[label] = text
    return bundle


def attach_context(state: dict[str, Any], bundle: Bundle) -> dict[str, Any]:
    if bundle:
        state["project_context"] = bundle.to_state()
    return state


# ---------------------------------------------------------------------- prompt assembly

PROMPT_DIR = Path(__file__).parent / "prompts"


def _instruction_block(instruction: str) -> str:
    """The researcher's own words, marked as theirs so the agent doesn't read them as
    state."""
    text = (instruction or "").strip()
    return f"## Instruction from the researcher\n\n{text}\n" if text else ""


REPO_TOOLS = "Read,Glob,Grep"
_CLAUDE_NAMES = {"claude", "claude.exe"}


def resolve_repos(repos: list[str] | None) -> list[str]:
    """Absolute, existing directories for `--repo`; `AgentError` names a bad one."""
    resolved: list[str] = []
    for raw in repos or []:
        path = Path(raw).expanduser().resolve()
        if not path.is_dir():
            raise AgentError(f"--repo {raw!r} is not a directory.")
        text = str(path)
        if text not in resolved:
            resolved.append(text)
    return resolved


def with_repos(command: list[str] | None, repos: list[str] | None) -> list[str] | None:
    """Give a `claude` agent read-only access to `repos` (`--repo PATH`).

    Adds `--add-dir PATH` for each repository — the only way `claude -p` may look
    outside the project it runs in — and, unless the command already chose its own
    `--allowedTools`, the read-only set `Read,Glob,Grep`. Never adds a tool that can
    write or run anything: `--repo` exists so the agent can *ground* a proposal in a
    repository that is not this one, not so it can change it. Returns `command`
    untouched when no repo is given.
    """
    resolved = resolve_repos(repos)
    if not resolved:
        return command
    argv = list(command or agent_command())
    if Path(argv[0]).name not in _CLAUDE_NAMES:
        raise AgentError(
            "--repo needs a `claude` agent command (it works by passing --add-dir and "
            "--allowedTools). For another agent, name the directories in --agent-cmd "
            "yourself."
        )
    has_tools = any(
        arg in {"--allowedTools", "--allowed-tools"}
        or arg.startswith(("--allowedTools=", "--allowed-tools="))
        for arg in argv
    )
    if not has_tools:
        argv += ["--allowedTools", REPO_TOOLS]
    for path in resolved:
        argv += ["--add-dir", path]
    return argv


def _repos_block(repos: list[str]) -> str:
    listing = "\n".join(f"- `{path}`" for path in repos)
    return (
        "## Repositories you may read\n\n"
        "You have read-only access (Read, Glob, Grep) to:\n\n"
        f"{listing}\n\n"
        "Use them to ground what you propose in what actually exists there: start with "
        "`CLAUDE.md`, `README`, memory/notes files and `docs/`, then the code and "
        "evaluation scripts the instruction is about (current implementation, "
        "available datasets and splits, existing metrics and their definitions). "
        "Prefer a definition, dataset or convention you find there over a generic "
        "default, and name the file it came from. Do not modify anything. What you "
        "read is background, never a measured result.\n"
    )


def build_prompt(
    workflow: str,
    state: dict[str, Any],
    *,
    instruction: str = "",
    root: Path | None = None,
    brief: str = "",
    repos: list[str] | None = None,
) -> str:
    """Fill in `prompts/{workflow}.md`, substituting `{{rules}}`, `{{state}}`,
    `{{instruction}}`, `{{brief}}`, `{{assumptions}}` (the shared policy for values
    nobody gave: look, default, choose, gap; also pulled in by `{{rules}}`).

    A project may override any prompt (or the shared rules) by placing its own copy at
    `.agents/skills/hiraku-agent/{workflow}.md` (or `_rules.md`) under `root` — checked
    first, so a project can tune the wording without touching this package.
    """
    if workflow not in WORKFLOWS:
        raise AgentError(
            f"Unknown workflow {workflow!r}. One of: {', '.join(WORKFLOWS)}"
        )

    def _read(name: str) -> str:
        if root is not None:
            local = root / ".agents" / "skills" / "hiraku-agent" / name
            if local.is_file():
                return local.read_text(encoding="utf-8")
        return (PROMPT_DIR / name).read_text(encoding="utf-8")

    text = _read(f"{workflow}.md")
    assumptions = _read("_assumptions.md")
    prompt = (
        text.replace("{{rules}}", _read("_rules.md"))
        .replace("{{assumptions}}", assumptions)
        .replace("{{state}}", json.dumps(state, indent=2, default=str, sort_keys=True))
        .replace("{{instruction}}", _instruction_block(instruction))
        .replace("{{brief}}", brief or "")
    )
    resolved = resolve_repos(repos)
    if resolved:
        block = _repos_block(resolved)
        marker = "## Output contract"
        if marker in prompt:
            head, tail = prompt.split(marker, 1)
            prompt = f"{head}{block}\n{marker}{tail}"
        else:
            prompt = f"{prompt.rstrip()}\n\n{block}"
    return prompt


# ------------------------------------------------------------------- command resolution


def agent_command(
    override: str | None = None,
    *,
    env_var: str = "HIRAKU_AGENT_CMD",
    default: str = DEFAULT_AGENT_CMD,
) -> list[str]:
    """Precedence: `override` (`--agent-cmd`), then `env_var`, then `default`.

    `env_var`/`default` let one caller (`workflows.run_implement`) resolve a
    differently-privileged fallback without changing what every other workflow falls
    back to — see `DEFAULT_IMPLEMENT_AGENT_CMD`'s comment. Every other call site uses
    the keyword defaults, which reproduce the original `HIRAKU_AGENT_CMD`/
    `DEFAULT_AGENT_CMD` behaviour exactly.

    See the module docstring: there is deliberately no project-config layer yet.
    """
    raw = override or os.environ.get(env_var) or default
    parts = shlex.split(raw)
    if not parts:
        raise AgentError("Agent command is empty.")
    return parts


def resolve_timeout(cli: int | None = None) -> int:
    return cli if cli and cli > 0 else DEFAULT_TIMEOUT


def agent_env(unset: list[str] | None = None) -> dict[str, str] | None:
    """The environment for the agent, minus any variables the caller asked to drop.

    `ANTHROPIC_API_KEY` is the one that matters in practice: when it is set, `claude`
    uses it in preference to the claude.ai login, which changes both which account is
    billed and whether the organisation's connectors load.
    """
    if not unset:
        return None
    env = dict(os.environ)
    for name in unset:
        env.pop(name, None)
    return env


PROMPT_PLACEHOLDER = "{prompt}"


def prompt_delivery(argv: list[str], prompt: str) -> tuple[list[str], str | None]:
    """Where the prompt goes: substituted, on stdin, or appended as the last argument.

    Appending was the only rule at first, and it breaks on a variadic flag —
    `claude --add-dir <dirs...>` accepts several directories, so `claude -p --add-dir
    ../code <prompt>` hands the prompt to `--add-dir` as a second directory and `claude`
    reports it was given no input at all. stdin has no such ambiguity, and `claude -p`
    reads it — its own error message says so. `{prompt}` anywhere in the command wins
    over both, for an agent that needs it somewhere else.
    """
    if any(PROMPT_PLACEHOLDER in arg for arg in argv):
        return [arg.replace(PROMPT_PLACEHOLDER, prompt) for arg in argv], None
    if Path(argv[0]).name in {"claude", "claude.exe"}:
        return list(argv), prompt
    return [*argv, prompt], None


def call_agent(
    prompt: str,
    *,
    command: list[str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    cwd: Path | None = None,
    progress: Callable[[float], None] | None = None,
    unset_env: list[str] | None = None,
) -> str:
    """Run the agent, calling `progress(elapsed_seconds)` while waiting.

    Popen rather than `subprocess.run` so the wait is interruptible and can report
    progress: `run` blocks silently until the process exits, and an agent call is
    minutes long. The pipes are drained on their own threads, because a reply large
    enough to fill the OS pipe buffer would otherwise deadlock the child against a
    parent that is only polling `wait(timeout=...)`.
    """
    argv = command or agent_command()
    if shutil.which(argv[0]) is None:
        raise AgentError(
            f"Agent command '{argv[0]}' not found on PATH. Install it, pass "
            f"--agent-cmd, or use --dry-run to print the prompt and run it yourself."
        )
    full_argv, stdin_text = prompt_delivery(argv, prompt)
    process = subprocess.Popen(  # noqa: S603
        full_argv,
        stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(cwd) if cwd else None,
        env=agent_env(unset_env),
        # Its own process group, so a timeout or Ctrl-C stops everything it started
        # (an agent CLI runs tools and shells as children), not just the CLI itself.
        start_new_session=os.name == "posix",
    )
    buffers: dict[str, str] = {"out": "", "err": ""}

    def drain(stream: Any, key: str) -> None:
        buffers[key] = stream.read() or ""

    def feed(text: str) -> None:
        try:
            assert process.stdin is not None
            process.stdin.write(text)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    readers = [
        threading.Thread(target=drain, args=(process.stdout, "out"), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, "err"), daemon=True),
    ]
    if stdin_text is not None:
        readers.append(threading.Thread(target=feed, args=(stdin_text,), daemon=True))
    for reader in readers:
        reader.start()

    started = time.monotonic()
    try:
        while True:
            try:
                process.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                elapsed = time.monotonic() - started
                if elapsed > timeout:
                    _stop_process_group(process)
                    raise AgentTimeout(
                        f"Agent timed out after {timeout}s. Raise it with --timeout, "
                        f"or run the prompt yourself: the exact text is in the log "
                        f"directory."
                    ) from None
                if progress:
                    progress(elapsed)
    except BaseException:
        # Ctrl-C included: the agent no longer shares our process group, so it would
        # otherwise keep running (and editing files) after we are gone.
        if process.poll() is None:
            _stop_process_group(process)
        raise
    for reader in readers:
        reader.join(timeout=5)

    if process.returncode != 0:
        parts = [f"Agent exited {process.returncode}."]
        for label, key in (("stderr", "err"), ("stdout", "out")):
            body = buffers[key].strip()
            if body:
                parts.append(f"--- {label} ---\n{body[:1200]}")
        combined = "\n".join(parts)
        advice = diagnose(buffers["err"] + "\n" + buffers["out"])
        if advice:
            combined += f"\n\n{advice}"
        raise AgentError(combined)
    if not buffers["out"].strip():
        message = "Agent produced no output."
        if buffers["err"].strip():
            message += f"\n--- stderr ---\n{buffers['err'].strip()[:1200]}"
        advice = diagnose(buffers["err"])
        raise AgentError(message + (f"\n\n{advice}" if advice else ""))
    return buffers["out"]


def _stop_process_group(process: subprocess.Popen[str], grace: float = 5.0) -> None:
    """SIGTERM the agent's whole process group, then SIGKILL whatever is left."""
    if os.name != "posix":
        process.kill()
        process.wait()
        return
    try:
        group = os.getpgid(process.pid)
    except ProcessLookupError:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(group, sig)
        except ProcessLookupError:
            break
        try:
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            continue
        if sig == signal.SIGTERM:
            # The leader is gone; make sure nothing it started outlives it.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(group, signal.SIGKILL)
        break
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=grace)


def _json_objects(text: str) -> list[dict[str, Any]]:
    """Every outermost JSON object in `text`, in order.

    Tries each `{` as a start, not only the first: prose before the reply ("use {x} as
    the key") must not hide the object that follows it. An object found is skipped as a
    whole, so its own nested objects are not returned separately.
    """
    decoder = json.JSONDecoder()
    found: list[dict[str, Any]] = []
    index = text.find("{")
    while index != -1:
        try:
            parsed, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            index = text.find("{", index + 1)
            continue
        if isinstance(parsed, dict):
            found.append(parsed)
        index = text.find("{", end)
    return found


def extract_json(text: str, keys: tuple[str, ...] = ()) -> dict[str, Any]:
    """Pull the reply's JSON object out of an agent's reply, fenced or bare.

    Fenced blocks come first, then bare objects. Among them, the first that has one of
    `keys` wins when `keys` is given; otherwise the one with the most top-level keys
    (a reply object has many, a stray example in prose few).
    """
    fenced = [
        obj
        for block in re.findall(r"```(?:json)?\s*(.*?)```", text, re.S)
        for obj in _json_objects(block)
    ]
    candidates = fenced or _json_objects(text)
    # `claude --output-format json` wraps the reply; unwrap it.
    for candidate in candidates:
        if isinstance(candidate.get("result"), str):
            return extract_json(candidate["result"], keys)
    if candidates:
        if keys:
            for candidate in candidates:
                if any(key in candidate for key in keys):
                    return candidate
        return max(candidates, key=len)
    raise AgentError(
        "The agent did not return a JSON object. First 500 characters of the reply:\n"
        + text[:500]
    )


# ------------------------------------------------------------------------------ logging

LOG_DIRNAME = "agent-logs"


def logs_root(config: HirakuConfig) -> Path:
    return config.hiraku_dir / LOG_DIRNAME


def _open_log(config: HirakuConfig, workflow: str, target: str) -> Path:
    stamp = _dt.datetime.now().strftime("%Y-%m-%dT%H%M%S")
    directory = logs_root(config) / f"{stamp}-{workflow}-{target}"
    suffix = 1
    while directory.exists():
        suffix += 1
        directory = logs_root(config) / f"{stamp}-{workflow}-{target}-{suffix}"
    directory.mkdir(parents=True)
    return directory


def read_logs(config: HirakuConfig, limit: int = 20) -> list[dict[str, Any]]:
    """The most recent calls, newest first. Each entry is what `meta.json` recorded."""
    root = logs_root(config)
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for directory in sorted(root.iterdir(), reverse=True):
        if not directory.is_dir():
            continue
        meta_path = directory / "meta.json"
        meta: dict[str, Any] = {"id": directory.name, "path": directory}
        if meta_path.is_file():
            try:
                meta.update(json.loads(meta_path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                meta["error"] = "meta.json is not readable"
        else:
            meta["error"] = "still running, or the process was killed"
        meta["id"], meta["path"] = directory.name, directory
        out.append(meta)
        if len(out) >= limit:
            break
    return out


@dataclass
class Hooks:
    """Where the caller gets told what is happening.

    An agent call is a minutes-long subprocess that prints nothing until it finishes.
    Without these the terminal sits blank and the only honest thing to conclude is that
    something has hung.
    """

    start: Callable[[dict[str, Any]], None] | None = None
    tick: Callable[[float], None] | None = None
    done: Callable[[dict[str, Any]], None] | None = None


@dataclass
class AgentRun:
    workflow: str
    prompt: str
    raw: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    written: list[Path] = field(default_factory=list)
    context: Bundle = field(default_factory=Bundle)
    log: Path | None = None
    seconds: float = 0.0


def invoke(
    config: HirakuConfig,
    workflow: str,
    target: str,
    prompt: str,
    bundle: Bundle,
    *,
    command: list[str] | None,
    timeout: int | None,
    hooks: Hooks | None,
    unset_env: list[str] | None = None,
    cwd: Path | None = None,
) -> tuple[str, Path, float]:
    """One agent call: announced before it starts, ticking while it waits, logged
    either way.

    Command, timeout and dropped environment variables are resolved here rather than at
    the call sites, so `--agent-cmd` / `HIRAKU_AGENT_CMD` has one documented precedence
    and the resolved values land in `meta.json`.
    """
    argv = command or agent_command()
    resolved_timeout = resolve_timeout(timeout)
    directory = _open_log(config, workflow, target)
    (directory / "prompt.md").write_text(prompt, encoding="utf-8")

    info: dict[str, Any] = {
        "workflow": workflow,
        "target": target,
        "command": " ".join(argv),
        "prompt_chars": len(prompt),
        "context": bundle.summary(),
        "log": str(directory.relative_to(config.root)),
        "started": _dt.datetime.now().isoformat(timespec="seconds"),
        "timeout": resolved_timeout,
        "unset_env": list(unset_env or []),
        "web": web_allowed(argv),
        "cwd": str(cwd or config.root),
    }

    def write_meta(extra: dict[str, Any]) -> None:
        (directory / "meta.json").write_text(
            json.dumps({**info, **extra}, indent=2, default=str), encoding="utf-8"
        )

    if hooks and hooks.start:
        hooks.start(info)
    started = time.monotonic()
    try:
        raw = call_agent(
            prompt,
            command=argv,
            timeout=resolved_timeout,
            cwd=cwd or config.root,
            progress=hooks.tick if hooks else None,
            unset_env=unset_env,
        )
    except Exception as exc:
        # Keep the failure beside the prompt. A run that died on authentication is the
        # one you most want the exact output for, and scrollback is not a record.
        (directory / "error.txt").write_text(str(exc), encoding="utf-8")
        failed = {"seconds": round(time.monotonic() - started, 1), "error": str(exc)}
        write_meta(failed)
        if hooks and hooks.done:
            hooks.done({**info, **failed})
        raise
    seconds = round(time.monotonic() - started, 1)
    (directory / "reply.txt").write_text(raw, encoding="utf-8")
    write_meta({"seconds": seconds, "reply_chars": len(raw), "error": None})
    if hooks and hooks.done:
        hooks.done({**info, "seconds": seconds, "error": None})
    return raw, directory, seconds
