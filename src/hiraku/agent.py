"""``hiraku-agent``: run workflow sessions from the terminal with a coding agent.

Each workflow is a command, and each of its events a subcommand:

    hiraku-agent collaborative --task cifar     # start, or resume, a session
    hiraku-agent collaborative implement        # send as the user, continue

The driver hands every pending instruction to a coding agent, such as
``claude -p``, and sends the event the agent writes to a file back to the
workflow. It stops where the user decides, at an instruction that requires a
user response, which the user answers with an event subcommand, and after the
agent carried out the final instruction, which takes no event. With
``--interactive``, each agent step is a conversation with the user in the
terminal instead of a headless run. The driver uses only the public workflow API
(`Workflow.run_start`, `Workflow.run_event`, and stored `Session`s), the same
one the MCP server uses, so both can continue the same session.
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import subprocess
import types
import typing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import click
from pydantic import BaseModel, create_model
from pydantic import ValidationError as PydanticValidationError
from pydantic.fields import FieldInfo

from .config import HirakuConfig
from .errors import HirakuError, ProjectNotFoundError, WorkflowError
from .session import Session
from .workflow import (
    Event,
    Workflow,
    discover_workflows,
    event_methods,
    instruction_text,
    load_workflow,
    summary,
    workflow_description,
)

EXIT_WAITING = 3
EXIT_NOT_READY = 4
DEFAULT_RETRIES = 2

# Instructions such as implementing or launching need editing and the shell.
_TOOLS = "Read,Glob,Grep,Write,Edit,Bash"
# The interactive command ends with "--": --allowedTools takes several values
# and would otherwise swallow the opening message appended after it.
DEFAULT_COMMANDS = {
    False: f"claude -p --allowedTools {_TOOLS}",
    True: f"claude --allowedTools {_TOOLS} --",
}
COMMAND_VARIABLES = {False: "HIRAKU_AGENT_CMD", True: "HIRAKU_AGENT_INTERACTIVE_CMD"}

Status = Literal["accepted", "rejected", "not_ready", "stale", "error"]


@dataclass(frozen=True)
class Options:
    """Options of the driver, shared by a workflow and its event commands."""

    session: str | None = None
    new: bool = False
    interactive: bool = False
    agent_cmd: str | None = None
    retries: int = DEFAULT_RETRIES


class _NoEvent(Exception):
    """The agent ended without writing an event."""


# Command line --------------------------------------------------------------------


class AgentGroup(click.Group):
    """The ``hiraku-agent`` command: one subcommand per project workflow."""

    def list_commands(self, ctx: click.Context) -> list[str]:
        try:
            return discover_workflows(HirakuConfig.discover())
        except ProjectNotFoundError:
            return []

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        try:
            config = HirakuConfig.discover()
            if cmd_name not in discover_workflows(config):
                return None
            workflow = load_workflow(cmd_name, config=config).bind(config)
        except (HirakuError, PydanticValidationError) as exc:
            return _unavailable(cmd_name, str(exc))
        return _workflow_command(workflow)

    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except (HirakuError, PydanticValidationError) as exc:
            raise click.ClickException(str(exc)) from exc


@click.group(name="hiraku-agent", cls=AgentGroup)
def main() -> None:
    """Run Hiraku workflows from the terminal with a coding agent.

    `hiraku-agent WORKFLOW` starts a session, or resumes the active one, and
    lets the agent carry out instructions until one needs your decision.
    `hiraku-agent WORKFLOW EVENT` answers it and continues. The agent runs
    headless (`claude -p` by default; set HIRAKU_AGENT_CMD or --agent-cmd), or
    in a conversation with you with --interactive (HIRAKU_AGENT_INTERACTIVE_CMD);
    either way the driver stops where you decide.
    """


def _driver_params() -> list[click.Parameter]:
    return [
        click.Option(
            ["--session", "session"],
            default=None,
            help="Session to continue; needed when several are active.",
        ),
        click.Option(
            ["--interactive", "interactive"],
            is_flag=True,
            default=False,
            help="Talk to the agent in this terminal for each instruction.",
        ),
        click.Option(
            ["--agent-cmd", "agent_cmd"],
            default=None,
            help="Agent command; the instruction is sent on standard input "
            "(appended as a message with --interactive).",
        ),
        click.Option(
            ["--retries", "retries"],
            type=click.IntRange(min=0),
            default=None,
            help=f"Retries after a rejected agent event [default: {DEFAULT_RETRIES}].",
        ),
    ]


def _workflow_command(workflow: Workflow) -> click.Group:
    start_model = workflow.start_arguments()
    driver = _driver_params()
    new = click.Option(
        ["--new", "new"],
        is_flag=True,
        default=False,
        help="Start a new session even if one is active.",
    )

    @click.pass_context
    def run(ctx: click.Context, **values: Any) -> None:
        if ctx.invoked_subcommand is not None:
            return
        options = _options(ctx)
        arguments = _arguments(start_model, values)
        session_id = _start_or_resume(workflow, options, arguments)
        # A session that --session names after it ended is shown, not run again.
        ended = Session.load(session_id, config=workflow.config).status != "active"
        ctx.exit(
            _drive(
                workflow,
                session_id,
                options,
                run_final=not options.session or not ended,
            )
        )

    group = click.Group(
        name=workflow.id,
        callback=run,
        params=[*driver, new, *_model_params(start_model, driver + [new])],
        invoke_without_command=True,
        help=workflow_description(workflow),
        short_help=summary(workflow_description(workflow)),
    )
    by_name: dict[str, list[Event]] = {}
    for method in event_methods(type(workflow)).values():
        by_name.setdefault(method.name, []).append(method)
    for name, methods in by_name.items():
        group.add_command(_event_command(workflow, name, methods))
    return group


def _event_command(
    workflow: Workflow, name: str, methods: list[Event]
) -> click.Command:
    """Return the subcommand that sends an event, in whichever state accepts it.

    Several methods may handle the event in different states; the options are
    the union of their arguments, which the current state's method validates.
    """
    driver = _driver_params()
    fields: dict[str, Any] = {}
    for method in methods:
        for field_name, field in method.arguments.model_fields.items():
            fields.setdefault(field_name, (field.annotation, field))
    model = create_model(f"{name}_options", **fields)
    if len(methods) == 1:
        description = methods[0].description
    else:
        description = "\n\n".join(
            f"In {', '.join(method.source)}: {method.description}" for method in methods
        )

    @click.pass_context
    def run(ctx: click.Context, **values: Any) -> None:
        options = _options(ctx)
        session = _select_for_event(workflow, name, options)
        result = workflow.run_event(
            session.id, session.instruction_id, name, _arguments(model, values)
        )
        _report(session.instruction_id, _heading(session.response), "you", name, result)
        status = result["status"]
        if status == "not_ready":
            ctx.exit(EXIT_NOT_READY)
        if status != "accepted":
            raise click.ClickException(result.get("message", status))
        ctx.exit(_drive(workflow, session.id, options))

    return click.Command(
        name=name,
        callback=run,
        params=[*driver, *_model_params(model, driver)],
        help=description,
        short_help=summary(methods[0].description),
    )


def _unavailable(name: str, message: str) -> click.Command:
    def run() -> None:
        raise click.ClickException(f"Workflow {name!r} is unavailable: {message}")

    return click.Command(name=name, callback=run, short_help="(unavailable)")


def _options(ctx: click.Context) -> Options:
    """Merge the driver options of an event command with its workflow's."""
    merged: dict[str, Any] = {}
    for context in (ctx.parent, ctx):
        if context is None:
            continue
        for key in ("session", "new", "interactive", "agent_cmd", "retries"):
            value = context.params.get(key)
            if value not in (None, False):
                merged[key] = value
    return Options(**merged)


def _model_params(
    model: type[BaseModel], reserved: list[click.Parameter]
) -> list[click.Option]:
    """Return one option per argument; the model validates the values."""
    taken = {param.name for param in reserved}
    params = []
    for name, field in model.model_fields.items():
        if name in taken:
            raise WorkflowError(
                f"Argument {name!r} clashes with a hiraku-agent option; rename it"
            )
        suffix = " [required]" if field.is_required() else ""
        params.append(
            click.Option(
                [f"--{name.replace('_', '-')}", name],
                default=None,
                metavar=_metavar(field),
                help=f"{field.description or ''}{suffix}".strip(),
            )
        )
    return params


def _metavar(field: FieldInfo) -> str:
    if typing.get_origin(field.annotation) is Literal:
        return (
            "["
            + "|".join(str(item) for item in typing.get_args(field.annotation))
            + "]"
        )
    return "TEXT" if _is_text(field.annotation) else "JSON"


def _arguments(model: type[BaseModel], values: dict[str, Any]) -> dict[str, Any]:
    """Convert option strings: text stays as is, other values parse as JSON."""
    arguments = {}
    for name, field in model.model_fields.items():
        value = values.get(name)
        if value is None:
            continue
        if not _is_text(field.annotation):
            # The model reports a value that is neither valid JSON nor valid.
            with contextlib.suppress(ValueError):
                value = json.loads(value)
        arguments[name] = value
    return arguments


def _is_text(annotation: Any) -> bool:
    if annotation is str:
        return True
    origin = typing.get_origin(annotation)
    arguments = [item for item in typing.get_args(annotation) if item is not type(None)]
    if origin is Literal:
        return all(isinstance(item, str) for item in arguments)
    if origin in (typing.Union, types.UnionType):
        return len(arguments) == 1 and _is_text(arguments[0])
    return False


# Sessions ------------------------------------------------------------------------


def _active(workflow: Workflow) -> list[Session]:
    return [
        Session.load(item["session_id"], config=workflow.config)
        for item in Session.list_all(config=workflow.config)
        if item.get("workflow") == workflow.id and item.get("status") == "active"
    ]


def _own(workflow: Workflow, session_id: str) -> Session:
    session = Session.load(session_id, config=workflow.config)
    if session.workflow != workflow.id:
        raise click.ClickException(
            f"Session {session_id!r} belongs to workflow {session.workflow!r}"
        )
    return session


def _start_or_resume(
    workflow: Workflow, options: Options, arguments: dict[str, Any]
) -> str:
    """Return the session to drive: the given or active one, or a new one."""
    if options.session is not None:
        if arguments or options.new:
            raise click.ClickException(
                "--session continues a session; it takes no start arguments or --new"
            )
        return _own(workflow, options.session).id
    active = _active(workflow)
    if active and not options.new:
        if arguments:
            raise click.ClickException(
                f"{workflow.id} already has an active session "
                f"({', '.join(item.id for item in active)}); continue it with "
                f"`hiraku-agent {workflow.id}`, or start another with --new"
            )
        if len(active) > 1:
            raise click.ClickException(_several(workflow, active))
        click.echo(f"session {active[0].id} (resumed)")
        return active[0].id
    response = workflow.run_start(arguments)
    if response["status"] != "accepted":
        raise click.ClickException(
            f"{workflow.id} did not start ({response['status']}): "
            f"{response.get('message')}"
        )
    click.echo(f"session {response['session_id']} (new)")
    return response["session_id"]


def _select_for_event(workflow: Workflow, event: str, options: Options) -> Session:
    """Return the active session whose current instruction accepts ``event``."""
    if options.session is not None:
        return _own(workflow, options.session)
    active = _active(workflow)
    if not active:
        raise click.ClickException(
            f"No active {workflow.id} session; start one with "
            f"`hiraku-agent {workflow.id}`"
        )
    candidates = [item for item in active if event in item.events]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        pending = "; ".join(
            f"{item.id} accepts {', '.join(item.events) or 'nothing'}"
            for item in active
        )
        raise click.ClickException(f"No active session accepts {event!r}: {pending}")
    raise click.ClickException(_several(workflow, candidates))


def _several(workflow: Workflow, sessions: list[Session]) -> str:
    return (
        f"Several {workflow.id} sessions are active "
        f"({', '.join(item.id for item in sessions)}); pass --session"
    )


# Driving -------------------------------------------------------------------------


def _drive(
    workflow: Workflow, session_id: str, options: Options, *, run_final: bool = True
) -> int:
    """Let the agent answer instructions until a user decision or the end.

    Args:
        run_final: Whether the agent carries out the final instruction of a
            completed session; otherwise it is only shown.

    Returns:
        The exit code: 0 when the session ended, `EXIT_WAITING` when it waits
        for the user, or `EXIT_NOT_READY` when it waits for evidence.
    """
    while True:
        session = Session.load(session_id, config=workflow.config)
        response = session.response
        if session.status == "completed" and run_final:
            _run_agent(workflow, response, 1, None, options)
        if session.status != "active":
            context = (response.get("instruction") or {}).get("context")
            click.echo(f"{session.status}: {json.dumps(context, ensure_ascii=False)}")
            return 0
        instruction = response["instruction"]
        if instruction["events"]["requires_user_response"]:
            _show_gate(workflow, response)
            return EXIT_WAITING
        pending = _pending(workflow, response)
        status = _resend(workflow, response, pending) if pending else None
        if status is None:
            try:
                status = _agent_turns(workflow, response, options)
            except _NoEvent:
                click.echo(
                    f"No event was written; instruction {response['instruction_id']} "
                    f"is still pending. Continue with `hiraku-agent {workflow.id}`."
                )
                return EXIT_WAITING
        if status == "not_ready":
            click.echo(
                "Waiting for evidence; check again later with "
                f"`hiraku-agent {workflow.id}`."
            )
            return EXIT_NOT_READY


def _agent_turns(
    workflow: Workflow, response: dict[str, Any], options: Options
) -> Status:
    """Run the agent on the current instruction until an event is not rejected."""
    error: str | None = None
    for attempt in range(1, options.retries + 2):
        try:
            name, arguments = _run_agent(workflow, response, attempt, error, options)
        except ValueError as exc:
            error = str(exc)
            click.echo(f"  rejected: {error}")
            continue
        result = workflow.run_event(
            response["session_id"], response["instruction_id"], name, arguments
        )
        _report(response["instruction_id"], _heading(response), "agent", name, result)
        status: Status = result["status"]
        if status == "rejected":
            error = result["message"]
            continue
        if status == "not_ready":
            _save_pending(workflow, response, name, arguments)
        if status in ("stale", "error"):
            raise click.ClickException(result["message"])
        return status
    raise click.ClickException(
        f"The agent's events were rejected {options.retries + 1} times; see "
        f"{_directory(workflow, response)}"
    )


def _run_agent(
    workflow: Workflow,
    response: dict[str, Any],
    attempt: int,
    error: str | None,
    options: Options,
) -> tuple[str, dict[str, Any]]:
    """Run the agent once and return the event it wrote.

    For a final instruction, which takes no event, return nothing.

    Raises:
        _NoEvent: If the agent wrote no event.
        ValueError: If the event file is malformed.
    """
    final = bool(response.get("completed"))
    if final:
        click.echo(f"[{response['instruction_id']} {_heading(response)}] final")
    directory = _directory(workflow, response) / str(attempt)
    directory.mkdir(parents=True, exist_ok=True)
    event_file = directory / "event.json"
    event_file.unlink(missing_ok=True)
    prompt_file = directory / "prompt.md"
    instruction_file = directory / "instruction.json"
    prompt_file.write_text(
        render_prompt(
            response, None if final else event_file, error, options.interactive
        ),
        encoding="utf-8",
    )
    instruction_file.write_text(
        json.dumps(response, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    command = shlex.split(_command(options))
    env = {
        **os.environ,
        "HIRAKU_SESSION_ID": response["session_id"],
        "HIRAKU_PROMPT_FILE": str(prompt_file),
        "HIRAKU_INSTRUCTION_FILE": str(instruction_file),
        **({} if final else {"HIRAKU_EVENT_FILE": str(event_file)}),
    }
    try:
        if options.interactive:
            message = (
                f"Read the Hiraku instruction in {prompt_file}. Tell me what you "
                "would do, and wait for my go-ahead before acting."
            )
            subprocess.run(
                [*command, message], cwd=workflow.config.root, env=env, check=False
            )
        else:
            _run_headless(command, prompt_file, directory, workflow.config.root, env)
    except FileNotFoundError as exc:
        raise click.ClickException(
            f"Cannot run the agent command {command[0]!r}; install it, or set "
            f"--agent-cmd or {COMMAND_VARIABLES[options.interactive]}"
        ) from exc
    if final:
        return "", {}
    if not event_file.is_file():
        if options.interactive:
            raise _NoEvent
        raise click.ClickException(
            f"The agent wrote no event; see {directory / 'output.txt'}"
        )
    return _read_event(event_file)


def _run_headless(
    command: list[str], prompt_file: Path, directory: Path, cwd: Path, env: dict
) -> None:
    with (
        prompt_file.open(encoding="utf-8") as prompt,
        (directory / "output.txt").open("w", encoding="utf-8") as log,
    ):
        process = subprocess.Popen(
            command,
            stdin=prompt,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=cwd,
            env=env,
        )
        assert process.stdout is not None
        for line in process.stdout:
            click.echo(f"  │ {line.rstrip()}")
            log.write(line)
        process.wait()


def _read_event(path: Path) -> tuple[str, dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"The event file is not JSON: {exc}") from exc
    if not isinstance(data, dict):
        data = {}
    name, arguments = data.get("event"), data.get("arguments") or {}
    if not isinstance(name, str) or not isinstance(arguments, dict):
        raise ValueError(
            'The event file must hold {"event": "<name>", "arguments": {...}}'
        )
    return name, arguments


def _command(options: Options) -> str:
    return (
        options.agent_cmd
        or os.environ.get(COMMAND_VARIABLES[options.interactive])
        or DEFAULT_COMMANDS[options.interactive]
    )


def _directory(workflow: Workflow, response: dict[str, Any]) -> Path:
    """Return the log directory of one instruction; sessions/ is not in Git."""
    return (
        workflow.config.sessions_dir
        / response["session_id"]
        / str(response["instruction_id"])
    )


def _pending(workflow: Workflow, response: dict[str, Any]) -> dict[str, Any] | None:
    path = _directory(workflow, response) / "pending.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _save_pending(
    workflow: Workflow, response: dict[str, Any], name: str, arguments: dict
) -> None:
    path = _directory(workflow, response) / "pending.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"event": name, "arguments": arguments}), "utf-8")


def _resend(
    workflow: Workflow, response: dict[str, Any], pending: dict[str, Any]
) -> Status | None:
    """Send an event that was not ready again; ``None`` if the agent must retry."""
    result = workflow.run_event(
        response["session_id"],
        response["instruction_id"],
        pending["event"],
        pending["arguments"],
    )
    _report(
        response["instruction_id"],
        _heading(response),
        "again",
        pending["event"],
        result,
    )
    status: Status = result["status"]
    if status != "not_ready":
        (_directory(workflow, response) / "pending.json").unlink(missing_ok=True)
    if status in ("stale", "error"):
        raise click.ClickException(result["message"])
    return None if status == "rejected" else status


# Output --------------------------------------------------------------------------


def render_prompt(
    response: dict[str, Any],
    event_file: Path | None,
    error: str | None = None,
    interactive: bool = False,
) -> str:
    """Render an instruction as a self-contained prompt for an agent.

    Without an event file, the instruction is final and takes no event.
    """
    parts = [instruction_text(response["instruction"])]
    if interactive:
        # As a conversation, the user decides when the step is carried out.
        mode = (
            "The user is in this conversation. Start by telling them what you "
            "understand the instruction asks for and what you would do. Do not "
            "change files or run commands until the user tells you to go ahead; "
            "then carry out the step"
            + ("." if event_file is None else " and write the event.")
        )
    else:
        mode = (
            "The user sees your output but cannot answer; decide yourself and do "
            "not ask questions."
        )
    if event_file is None:
        parts.append(
            f"---\n{mode}\n\nThis is the final instruction of the session; it "
            "takes no event. Stop when you are done."
        )
        return "\n\n".join(parts) + "\n"
    parts.append(
        f"---\n{mode}\n\nWhen you are done, write exactly one event as a JSON "
        f"object to `{event_file}`:\n"
        '`{"event": "<one of events.accepted>", "arguments": {...}}`, with the '
        "arguments the option's schema describes. The session and instruction "
        "IDs are not needed."
    )
    if error is not None:
        parts.append(
            f"Your previous event was rejected: {error}\nWrite a corrected event."
        )
    return "\n\n".join(parts) + "\n"


def _heading(response: dict[str, Any]) -> str:
    instruction = response.get("instruction") or {}
    first = (instruction.get("text") or "").splitlines()[:1]
    return first[0].lstrip("#").strip() if first else ""


def _report(
    instruction_id: int, heading: str, actor: str, name: str, result: dict[str, Any]
) -> None:
    status = result["status"]
    message = f": {result['message']}" if status != "accepted" else ""
    click.echo(f"[{instruction_id} {heading}] {actor} → {name}  {status}{message}")


def _show_gate(workflow: Workflow, response: dict[str, Any]) -> None:
    instruction = response["instruction"]
    click.echo(
        f"[{response['instruction_id']} {_heading(response)}] waiting for you "
        f"(session {response['session_id']})"
    )
    if instruction.get("context") is not None:
        context = json.dumps(instruction["context"], indent=1, ensure_ascii=False)
        click.echo("\n".join(f"  {line}" for line in context.splitlines()))
    click.echo("Send one of:")
    for item in instruction["events"]["accepted"]:
        flags = " ".join(
            f"--{name.replace('_', '-')} …"
            for name in item.get("arguments", {}).get("properties", {})
        )
        command = f"hiraku-agent {workflow.id} {item['event']} {flags}".strip()
        description = f"  # {item['description']}" if item.get("description") else ""
        click.echo(f"  {command}{description}")


__all__ = ["DEFAULT_COMMANDS", "main", "render_prompt"]
