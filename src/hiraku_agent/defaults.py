"""Project-level defaults for what the agent may read: `<hiraku_dir>/agent.yaml`.

Claude Code reads the project's notes and code without being told to. `hiraku-agent`
only reads what a call names, so a researcher ended up repeating `--repo ...` and
`--context-id ...` on every command, and a call that forgot them silently planned
without the values that live in the code. This file names them once::

    repo:
      - ~/work/mapillary_alignment      # read-only, as with --repo
    context_id: matcher-context         # a `Context` record, as with --context-id
    context:
      - notes/benchmark-notes.md        # literal files, as with --context

Every agent command that takes `--repo` / `--context` / `--context-id` merges these in:
the repositories and files are added to whatever the call names, and an explicit
`--context-id` wins over the default one. A listed repository that does not exist on
this machine is skipped with a notice rather than failing, so one file works on a laptop
and on a cluster whose checkouts live elsewhere. Set `HIRAKU_AGENT_NO_DEFAULTS=1` to
ignore the file for one call.

The file only widens what the agent may *read*. It never makes a repository writable and
never changes which records a command may edit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from hiraku.config import HirakuConfig

from .errors import AgentError

FILENAME = "agent.yaml"
OPT_OUT_ENV = "HIRAKU_AGENT_NO_DEFAULTS"


@dataclass
class AgentDefaults:
    repos: list[str] = field(default_factory=list)
    context_paths: list[str] = field(default_factory=list)
    context_id: str | None = None
    # Optional write protection for every agent call (`sandbox.py`). Off by default.
    sandbox: bool = False
    sandbox_allow_write: list[str] = field(default_factory=list)
    sandbox_deny_write: list[str] = field(default_factory=list)
    # Seconds an `implement` call may take (default `engine.DEFAULT_IMPLEMENT_TIMEOUT`).
    implement_timeout: int | None = None

    def is_empty(self) -> bool:
        return not (
            self.repos
            or self.context_paths
            or self.context_id
            or self.sandbox
            or self.sandbox_allow_write
            or self.sandbox_deny_write
            or self.implement_timeout
        )


def defaults_path(config: HirakuConfig) -> Path:
    return config.hiraku_dir / FILENAME


def _as_list(value: Any, key: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    raise AgentError(f"{FILENAME}: {key!r} must be a path or a list of paths.")


def load_defaults(config: HirakuConfig) -> AgentDefaults:
    """The project's agent defaults; empty when the file is absent."""
    path = defaults_path(config)
    if not path.is_file():
        return AgentDefaults()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise AgentError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise AgentError(f"{path} must be a mapping (repo, context_id, context).")
    context_id = data.get("context_id")
    return AgentDefaults(
        repos=_as_list(data.get("repo", data.get("repos")), "repo"),
        context_paths=_as_list(data.get("context"), "context"),
        context_id=str(context_id) if context_id else None,
        sandbox=_as_bool(data.get("sandbox"), "sandbox"),
        sandbox_allow_write=_as_list(
            data.get("sandbox_allow_write"), "sandbox_allow_write"
        ),
        sandbox_deny_write=_as_list(
            data.get("sandbox_deny_write"), "sandbox_deny_write"
        ),
        implement_timeout=_as_seconds(
            data.get("implement_timeout"), "implement_timeout"
        ),
    )


def _as_seconds(value: Any, key: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    raise AgentError(f"{FILENAME}: {key!r} must be a positive number of seconds.")


def _as_bool(value: Any, key: str) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"on", "true", "yes", "1"}:
        return True
    if text in {"off", "false", "no", "0", ""}:
        return False
    raise AgentError(f"{FILENAME}: {key!r} must be on or off, not {value!r}.")


def save_defaults(defaults: AgentDefaults, config: HirakuConfig) -> Path:
    path = defaults_path(config)
    data: dict[str, Any] = {}
    if defaults.repos:
        data["repo"] = defaults.repos
    if defaults.context_id:
        data["context_id"] = defaults.context_id
    if defaults.context_paths:
        data["context"] = defaults.context_paths
    if defaults.sandbox:
        data["sandbox"] = "on"
    if defaults.sandbox_allow_write:
        data["sandbox_allow_write"] = defaults.sandbox_allow_write
    if defaults.sandbox_deny_write:
        data["sandbox_deny_write"] = defaults.sandbox_deny_write
    if defaults.implement_timeout:
        data["implement_timeout"] = defaults.implement_timeout
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _resolve_repo(raw: str, config: HirakuConfig) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (config.root / path)


def merge_defaults(
    workflow_kwargs: dict[str, Any], config: HirakuConfig
) -> tuple[dict[str, Any], list[str]]:
    """Add the project defaults to a workflow call's `repo_paths` / `context_paths` /
    `context_id`, for whichever of them that workflow takes. Returns the merged kwargs
    and human-readable notices (what was added, what was skipped)."""
    if os.environ.get(OPT_OUT_ENV):
        return workflow_kwargs, []
    defaults = load_defaults(config)
    if defaults.is_empty():
        return workflow_kwargs, []
    merged = dict(workflow_kwargs)
    notes: list[str] = []
    if "repo_paths" in merged and defaults.repos:
        repos = list(merged.get("repo_paths") or [])
        for raw in defaults.repos:
            path = _resolve_repo(raw, config)
            if not path.is_dir():
                notes.append(f"default repo {raw} not found on this machine, skipped")
            elif str(path) not in repos:
                repos.append(str(path))
                notes.append(f"reading default repo {path}")
        merged["repo_paths"] = repos
    if "context_paths" in merged and defaults.context_paths:
        files = list(merged.get("context_paths") or [])
        for raw in defaults.context_paths:
            path = _resolve_repo(raw, config)
            if not path.is_file():
                notes.append(f"default context file {raw} not found, skipped")
            elif str(path) not in files:
                files.append(str(path))
                notes.append(f"reading default context file {path}")
        merged["context_paths"] = files
    if "context_id" in merged and defaults.context_id and not merged.get("context_id"):
        merged["context_id"] = defaults.context_id
        notes.append(f"reading default context {defaults.context_id}")
    return merged, notes
