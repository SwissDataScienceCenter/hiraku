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

import datetime as _dt
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hiraku.config import HirakuConfig

from .errors import AgentError

DEFAULT_AGENT_CMD = "claude -p"
DEFAULT_TIMEOUT = 900

WORKFLOWS = ("design", "discover", "triage", "analyze")

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


def build_prompt(
    workflow: str,
    state: dict[str, Any],
    *,
    instruction: str = "",
    root: Path | None = None,
    brief: str = "",
) -> str:
    """Fill in `prompts/{workflow}.md`, substituting `{{rules}}`, `{{state}}`,
    `{{instruction}}`, `{{brief}}`.

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
    rules = _read("_rules.md")
    return (
        text.replace("{{rules}}", rules)
        .replace("{{state}}", json.dumps(state, indent=2, default=str, sort_keys=True))
        .replace("{{instruction}}", _instruction_block(instruction))
        .replace("{{brief}}", brief or "")
    )


# ------------------------------------------------------------------- command resolution


def agent_command(override: str | None = None) -> list[str]:
    """Precedence: `override` (`--agent-cmd`), then `HIRAKU_AGENT_CMD`, then the
    default.

    See the module docstring: there is deliberately no project-config layer yet.
    """
    raw = override or os.environ.get("HIRAKU_AGENT_CMD") or DEFAULT_AGENT_CMD
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
    while True:
        try:
            process.wait(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - started
            if elapsed > timeout:
                process.kill()
                process.wait()
                raise AgentError(
                    f"Agent timed out after {timeout}s. Raise it with --timeout, or "
                    f"run the prompt yourself: the exact text is in the log directory."
                ) from None
            if progress:
                progress(elapsed)
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


def extract_json(text: str) -> dict[str, Any]:
    """Pull one JSON object out of an agent's reply, fenced or bare."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = [fenced.group(1)] if fenced else []
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : index + 1])
                    break
        break
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            # `claude --output-format json` wraps the reply; unwrap it.
            if "result" in parsed and isinstance(parsed["result"], str):
                return extract_json(parsed["result"])
            return parsed
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
