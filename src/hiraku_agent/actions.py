"""`Action`/`Context`: saved, reusable configuration for one agent workflow call.

`hiraku.agent.Action` and `hiraku.agent.Context` are plain `Record`s — file-backed
data, never code (`hiraku.agent`'s own module docstring says so outright: "execution
is out of scope"). This module is the only place their fields get translated into the
keyword arguments `run_design`/`run_discover`/`run_triage`/`run_analyze`/`run_draft`
already take in `workflows.py`; those functions stay unaware an `Action` was ever
involved — the same call happens whether its settings came from `--agent-cmd`/
`--timeout`/`--context` on the command line or from a saved record. See
`hiraku/program-action-merge-proposal-zou.md` (the "Phase 0" section) for the design
this implements, and `loop.py` for how a `Program` reuses this same translation across
a whole bounded loop rather than one call.

Both record types carry their configuration as *extra* fields (`Record`'s own
`model_config = ConfigDict(extra="allow")`), not typed ones — `hiraku.agent.Action`
has exactly one real field today (`entity`), and `Context` has none. That is
deliberate: it lets this module ship without any change to `hiraku` core. See the
proposal doc's Phase 2 for promoting the fields below into typed ones, once they have
earned their keep.

- `Action` extra fields this module reads: `agent_cmd`, `timeout`, `context` (a list of
  literal paths, or a single `Context` id string).
- `Context` extra field this module reads: `paths` (a list of literal paths, the same
  shape `--context` already takes).

`Action.entity` is used the way `hiraku_agent` needs it, not the way `hiraku` core
might one day want it (see the proposal's open question 2): an `Action` here is scoped
to the task id it configures a call for, so `hiraku action create <action-id> --set
entity=<task-id> --set workflow=design --set agent_cmd='claude -p' ...` is already
enough to make a `--action <action-id>` flag work, with zero `hiraku` core changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hiraku.agent import load_action, load_context
from hiraku.config import HirakuConfig

from .engine import agent_command
from .errors import AgentError


@dataclass
class ResolvedAction:
    """What an `Action` record contributes as call defaults.

    Every field here is a default, never an override: a `--agent-cmd`/`--timeout`/
    `--context` the caller already gave explicitly always wins, exactly the way
    `engine.agent_command()` already lets `--agent-cmd` win over `HIRAKU_AGENT_CMD`.
    """

    command: list[str] | None = None
    timeout: int | None = None
    context_paths: list[str] = field(default_factory=list)


def resolve_context(context_id: str, *, config: HirakuConfig) -> list[str]:
    """The file paths a `Context` record names (`Context.paths`), in the same shape
    `--context` already takes."""
    context = load_context(context_id, config=config)
    paths = (context.model_extra or {}).get("paths")
    if not isinstance(paths, list):
        return []
    return [str(path) for path in paths]


def resolve_action(
    action_id: str, *, entity: str, config: HirakuConfig
) -> ResolvedAction:
    """Translate an `Action` record's saved defaults into call overrides.

    `entity` is the task id the action is scoped to (`Action.entity` — see this
    module's docstring). `context` may itself name a `Context` id (a string) rather
    than listing paths directly, so an `Action` can point at a `Context` record
    instead of duplicating its path list.
    """
    action = load_action(action_id, entity=entity, config=config)
    extra = action.model_extra or {}
    agent_cmd = extra.get("agent_cmd")
    command = agent_command(str(agent_cmd)) if agent_cmd else None
    timeout = extra.get("timeout")
    context = extra.get("context")
    if isinstance(context, str):
        context_paths = resolve_context(context, config=config)
    elif isinstance(context, list):
        context_paths = [str(path) for path in context]
    else:
        context_paths = []
    return ResolvedAction(
        command=command,
        timeout=timeout if isinstance(timeout, int) else None,
        context_paths=context_paths,
    )


def apply_overrides(
    action_id: str | None,
    context_id: str | None,
    *,
    entity: str | None,
    command: list[str] | None,
    timeout: int | None,
    context_paths: list[str] | None,
    config: HirakuConfig,
) -> tuple[list[str] | None, int | None, list[str]]:
    """Merge an `Action`/`Context` record's saved defaults under whatever the caller
    already gave explicitly. Returns `(command, timeout, context_paths)`, ready to pass
    straight into `engine.invoke`/`engine.gather_context`.
    """
    merged_paths = list(context_paths or [])
    if action_id is not None:
        if entity is None:
            raise AgentError(
                "--action needs a known task id to resolve against — pass --task-id "
                "(or --task)."
            )
        resolved = resolve_action(action_id, entity=entity, config=config)
        command = command or resolved.command
        timeout = timeout if timeout is not None else resolved.timeout
        merged_paths += resolved.context_paths
    if context_id is not None:
        merged_paths += resolve_context(context_id, config=config)
    return command, timeout, merged_paths
