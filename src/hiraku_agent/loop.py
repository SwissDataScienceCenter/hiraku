"""`hiraku-agent loop`: drive a bounded research loop from a `Program` record.

`schedule.suggest_next` says *that* one thing is next for a task; this module is what
repeatedly asks it and, for the actions that need judgement, calls into `workflows.py`
through whichever `Action` the `Program` binds to that workflow — reusing the exact
same `Action`/`Context` translation `actions.py` already does for one `--action` call,
just applied across a whole loop instead of a single command. See
`hiraku/program-action-merge-proposal-zou.md` (the "Phase 1" section) for the design
this implements.

Nothing here executes what `suggest_next` cannot already name. `launch` is a real
command a person or a CI job runs, and `approve` is a person's decision — the loop
stops and reports those rather than guessing at them, since that is `Program`'s whole
job: it bounds *what may run automatically*, never *whether a human reviews it*.
`implement` is no longer in that excluded set: `workflows.run_implement` is an LLM call
too, just one with real file-write and commit side effects (`engine.py`'s
`DEFAULT_IMPLEMENT_AGENT_CMD`) instead of only returning JSON. It is safe to drive
automatically here for the same reason everything else in `LLM_WORKFLOWS` is: the
approval gate in `gates.py` is unmoved by anything below, `is_approved()` already
decided it, inside `suggest_next()`, before this module ever sees the suggestion — and
`run_implement` checks it again itself (see its own docstring for why both checks
exist).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hiraku.agent import Program, load_action, load_program
from hiraku.config import HirakuConfig
from hiraku.data import load_task

from .engine import Hooks
from .errors import AgentError
from .node import list_nodes
from .schedule import suggest_next
from .workflows import run_analyze, run_design, run_discover, run_implement, run_triage

# A loop with no `max_steps` and a `Program` that sets none of `max_experiments`/
# `max_runs`/`time_budget` either would otherwise have no bound at all — this is the
# floor under that, not a substitute for setting a real one.
_DEFAULT_MAX_STEPS = 25

# The workflows this module can drive automatically — everything `suggest_next` can say
# that is an LLM call, as opposed to a real command (`launch`) or a person's decision
# (`approve`). `discover` is included for completeness even though `schedule.
# suggest_next` never names it today (`design` is what it suggests when a task has no
# experiments yet) — a `Program` may still bind it for whenever the scheduling policy
# grows to suggest it. `implement` belongs here precisely because `run_implement`
# re-checks `is_approved` itself before writing anything — see its docstring.
LLM_WORKFLOWS = {
    "design": run_design,
    "discover": run_discover,
    "implement": run_implement,
    "triage": run_triage,
    "analyze": run_analyze,
}


@dataclass
class LoopStep:
    """One iteration: what `suggest_next` said, and what running it wrote."""

    action: str
    action_id: str
    node: str | None
    why: str
    written: list[Path] = field(default_factory=list)


@dataclass
class LoopResult:
    steps: list[LoopStep] = field(default_factory=list)
    stopped: str = ""


def _duration_seconds(value: Any) -> float | None:
    """Parse a `Program.time_budget` value: a plain number of seconds, or a short
    duration string like `2h`, `90m`, `45s` (matching `docs/concepts.md`'s own
    `--time-budget 2h` example). `None`, or anything unparseable, means no budget."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().lower()
    units = {"h": 3600.0, "m": 60.0, "s": 1.0}
    if text[-1] in units:
        number = text[:-1]
        try:
            return float(number) * units[text[-1]]
        except ValueError:
            return None
    try:
        return float(text)
    except ValueError:
        return None


def _bound_actions(
    program: Program, *, task_id: str, config: HirakuConfig
) -> dict[str, str]:
    """`{workflow: action_id}`, from the `Action` ids `program.actions` lists.

    Raises if the program names no actions, an action names a workflow this module
    cannot drive, or two actions are bound to the same workflow — each is a
    configuration mistake better caught before the first agent call than discovered
    mid-loop.
    """
    action_ids = (program.model_extra or {}).get("actions")
    if not isinstance(action_ids, list) or not action_ids:
        raise AgentError(
            f"Program {program.id!r} names no actions — set an 'actions' list of "
            f"Action ids on it (each with its own 'workflow' field) before looping."
        )
    bound: dict[str, str] = {}
    for raw_id in action_ids:
        action_id = str(raw_id)
        action = load_action(action_id, entity=task_id, config=config)
        workflow = str((action.model_extra or {}).get("workflow") or "")
        if workflow not in LLM_WORKFLOWS:
            raise AgentError(
                f"Action {action_id!r} on program {program.id!r} names workflow "
                f"{workflow!r}, not one of {sorted(LLM_WORKFLOWS)}."
            )
        if workflow in bound:
            raise AgentError(
                f"Program {program.id!r} binds two actions to workflow {workflow!r} "
                f"({bound[workflow]!r} and {action_id!r}) — ambiguous, pick one."
            )
        bound[workflow] = action_id
    return bound


def _budget_reason(
    program: Program, task_id: str, *, config: HirakuConfig, started: float
) -> str | None:
    """The first budget field the current state has exceeded, or `None`."""
    extra = program.model_extra or {}
    nodes = list_nodes(task=task_id, config=config)

    max_experiments = extra.get("max_experiments")
    if isinstance(max_experiments, int) and len(nodes) >= max_experiments:
        return f"max_experiments ({max_experiments}) reached: {len(nodes)} nodes exist"

    max_runs = extra.get("max_runs")
    if isinstance(max_runs, int):
        total_runs = sum(len(node.runs(config=config)) for node in nodes)
        if total_runs >= max_runs:
            return f"max_runs ({max_runs}) reached: {total_runs} runs recorded"

    budget_seconds = _duration_seconds(extra.get("time_budget"))
    if budget_seconds is not None and (time.monotonic() - started) >= budget_seconds:
        return f"time_budget ({extra['time_budget']}) reached"

    return None


def run_loop(
    program_id: str,
    task_id: str,
    *,
    max_steps: int | None = None,
    hooks: Hooks | None = None,
    config: HirakuConfig | None = None,
) -> LoopResult:
    """Drive `suggest_next(task_id)` using `program_id`'s bound actions until a budget
    is hit, a step makes no progress, a suggested action needs a person or a real
    command rather than an LLM call, or there is nothing left to schedule.

    `max_steps` overrides `program_id`'s own budget fields when given; when neither
    supplies one, `_DEFAULT_MAX_STEPS` still bounds the loop — a `Program` is meant to
    be a *bounded* loop, and an unset budget should not silently mean unbounded.
    """
    selected = config or HirakuConfig.discover()
    program = load_program(program_id, config=selected)
    load_task(task_id, config=selected)  # fail fast on a bad task id
    bound = _bound_actions(program, task_id=task_id, config=selected)
    has_own_budget = any(
        (program.model_extra or {}).get(key) is not None
        for key in ("max_experiments", "max_runs", "time_budget")
    )
    effective_max_steps = max_steps
    if effective_max_steps is None and not has_own_budget:
        effective_max_steps = _DEFAULT_MAX_STEPS

    result = LoopResult()
    started = time.monotonic()
    steps_taken = 0
    while True:
        if effective_max_steps is not None and steps_taken >= effective_max_steps:
            result.stopped = f"max_steps ({effective_max_steps}) reached"
            break
        budget_reason = _budget_reason(
            program, task_id, config=selected, started=started
        )
        if budget_reason:
            result.stopped = budget_reason
            break

        suggestion = suggest_next(task_id, config=selected)
        next_action = suggestion["action"]
        if next_action == "exhausted":
            result.stopped = "nothing left to schedule"
            break
        if next_action not in bound:
            result.stopped = (
                f"next action is {next_action!r} ({suggestion['why']}) — that needs a "
                f"person or a real command, not an LLM call, so the loop stops here"
            )
            break

        action_id = bound[next_action]
        runner = LLM_WORKFLOWS[next_action]
        call_kwargs: dict[str, Any] = {
            "action": action_id,
            "config": selected,
            "hooks": hooks,
        }
        if next_action in {"triage", "analyze", "implement"}:
            run = runner(suggestion["node"], task_id=task_id, **call_kwargs)
        else:
            run = runner(task_id, **call_kwargs)

        result.steps.append(
            LoopStep(
                action=next_action,
                action_id=action_id,
                node=suggestion.get("node"),
                why=suggestion["why"],
                written=list(run.written),
            )
        )
        steps_taken += 1
        if not run.written:
            # The state this step could act on has not changed, so calling it again
            # would just repeat the same call forever rather than make progress.
            result.stopped = (
                f"{next_action} step wrote nothing — stopping rather than repeating it"
            )
            break
    return result
