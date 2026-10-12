"""Optional write protection for every agent call: `sandbox: on` in `hiraku/agent.yaml`.

An agent that only reads (every workflow but `implement`) cannot write anyway. One
that writes code and runs commands (`implement`) could change files outside its
worktree — a production repository the experiment depends on, its outputs, a pinned
copy. With the sandbox on, every `claude` call gets a `--settings` block that turns on
Claude Code's own protection:

- `permissions.deny` `Edit(//path/**)` for each protected path, which stops the Edit
  and Write tools (and Bash output redirects) there;
- the Bash sandbox (`sandbox.enabled`), which confines every Bash command and its
  child processes at the operating-system level: writes are allowed only in the
  agent's working directory (its worktree), the project's own git directory (where a
  worktree's commits go), the temp directory and `sandbox_allow_write`, and never in
  `sandbox_deny_write`;
- `failIfUnavailable` and `allowUnsandboxedCommands: false`, so a sandbox that cannot
  start is an error, never a silent fallback to no protection.

The protected paths are the project's default repositories (`repo` in `agent.yaml`)
plus `sandbox_deny_write`. Reads are not restricted.

On Linux the Bash sandbox needs `bwrap` (bubblewrap) and `socat` on PATH, and
unprivileged user namespaces. The two tools are checked before the call, so their
absence costs nothing; anything else that stops the sandbox makes Claude Code refuse to
start (`failIfUnavailable`).

It is off unless a project turns it on, and `HIRAKU_AGENT_NO_SANDBOX=1` (or
`hiraku-agent --no-sandbox ...`) turns it off for one call, with a notice.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from hiraku.config import HirakuConfig

from .defaults import load_defaults
from .errors import AgentError

OPT_OUT_ENV = "HIRAKU_AGENT_NO_SANDBOX"
_CLAUDE_NAMES = {"claude", "claude.exe"}
_LINUX_TOOLS = ("bwrap", "socat")


def _absolute(raw: str, config: HirakuConfig) -> str:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = config.root / path
    return str(path.resolve())


def _project_git_dir(config: HirakuConfig) -> list[str]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(config.root), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0 or not completed.stdout.strip():
        return []
    path = Path(completed.stdout.strip())
    if not path.is_absolute():
        path = config.root / path
    return [str(path.resolve())]


def sandbox_settings(config: HirakuConfig) -> dict[str, Any] | None:
    """The `--settings` block for this project's agent calls, or None when the sandbox
    is off (not configured, or turned off for this call)."""
    defaults = load_defaults(config)
    if not defaults.sandbox or os.environ.get(OPT_OUT_ENV):
        return None
    deny = list(
        dict.fromkeys(
            _absolute(raw, config)
            for raw in [*defaults.repos, *defaults.sandbox_deny_write]
        )
    )
    allow = list(
        dict.fromkeys(
            [
                # `implement` commits from a worktree whose git directory lives in the
                # project's own repository, outside the worktree.
                *_project_git_dir(config),
                *(_absolute(raw, config) for raw in defaults.sandbox_allow_write),
            ]
        )
    )
    filesystem: dict[str, Any] = {"denyWrite": deny}
    if allow:
        filesystem["allowWrite"] = allow
    return {
        # `//` makes a permission rule path absolute (a single `/` would be relative to
        # the settings source).
        "permissions": {"deny": [f"Edit(/{path}/**)" for path in deny]},
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,
            "allowUnsandboxedCommands": False,
            "filesystem": filesystem,
        },
    }


def missing_requirements() -> list[str]:
    """What this machine lacks for the Bash sandbox; empty when nothing is known to be
    missing. Only Linux needs extra tools (macOS uses its built-in sandbox)."""
    if not sys.platform.startswith("linux"):
        return []
    return [tool for tool in _LINUX_TOOLS if shutil.which(tool) is None]


def apply_sandbox(
    argv: list[str], config: HirakuConfig
) -> tuple[list[str], dict[str, Any] | None]:
    """`argv` with the sandbox settings added, and a summary for logs; unchanged when
    the sandbox is off. Raises before any call when it is on but cannot work."""
    settings = sandbox_settings(config)
    if settings is None:
        if load_defaults(config).sandbox:
            return argv, {"enabled": False, "reason": f"{OPT_OUT_ENV} is set"}
        return argv, None
    if Path(argv[0]).name not in _CLAUDE_NAMES:
        raise AgentError(
            "The project's sandbox is on, but the agent command is not `claude`, so "
            "it cannot be applied. Use a claude command, or turn it off for this "
            f"call with `hiraku-agent --no-sandbox ...` ({OPT_OUT_ENV}=1)."
        )
    if any(arg == "--settings" or arg.startswith("--settings=") for arg in argv):
        raise AgentError(
            "The project's sandbox is on, but the agent command already passes "
            "--settings; merge the sandbox into your settings file and run with "
            "--no-sandbox, or drop your --settings."
        )
    missing = missing_requirements()
    if missing:
        raise AgentError(
            "The project's sandbox is on, but this machine lacks "
            + ", ".join(missing)
            + " (Claude Code's Bash sandbox on Linux needs bubblewrap and socat, and "
            "unprivileged user namespaces). Install them or ask the cluster admins, or "
            "run without protection for this call: `hiraku-agent --no-sandbox ...`."
        )
    summary = {
        "enabled": True,
        "deny_write": settings["sandbox"]["filesystem"]["denyWrite"],
        "allow_write": settings["sandbox"]["filesystem"].get("allowWrite", []),
    }
    return [*argv, "--settings", json.dumps(settings)], summary
