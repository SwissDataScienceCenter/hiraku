"""Terminal workflow driver: dispatch workers and submit their event files."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import typing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import click
from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError
from pydantic.fields import FieldInfo

from .config import HirakuConfig
from .errors import HirakuError, ProjectNotFoundError, WorkflowError
from .workflows.graph import Event, _is_text
from .workflows.session import Session, list_sessions, load_session
from .workflows.workflow import (
    Instruction,
    Workflow,
    _workflow_ids,
    load_workflow,
    summary,
    validation_message,
)

EXIT_WAITING = 3
EXIT_NOT_READY = 4

# Instructions such as implementing or launching need editing and the shell.
_TOOLS = "Read,Glob,Grep,Write,Edit,Bash"
DEFAULT_COMMANDS = {
    False: f"claude -p --allowedTools {_TOOLS}",
    True: f"claude --allowedTools {_TOOLS}",
}
COMMAND_VARIABLES = {False: "HIRAKU_AGENT_CMD", True: "HIRAKU_AGENT_INTERACTIVE_CMD"}


class _Submission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: str
    arguments: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class Options:
    """Project and driver options shared by workflow and event commands."""

    config: HirakuConfig

    session: str | None = None
    new: bool = False
    interactive: bool = False
    agent_cmd: str | None = None


# Command line --------------------------------------------------------------------


class AgentGroup(click.Group):
    """The ``hiraku-agent`` command: one subcommand per project workflow."""

    def list_commands(self, ctx: click.Context) -> list[str]:
        try:
            return _workflow_ids(HirakuConfig.discover())
        except ProjectNotFoundError:
            return []

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        registered = super().get_command(ctx, cmd_name)
        if registered is not None:
            return registered
        try:
            config = HirakuConfig.discover()
            if cmd_name not in _workflow_ids(config):
                return None
            workflow = load_workflow(cmd_name, config=config)
        except (HirakuError, PydanticValidationError) as exc:
            return _unavailable(cmd_name, str(exc))
        return _workflow_command(workflow, config)

    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except PydanticValidationError as exc:
            raise click.ClickException(validation_message(exc)) from exc
        except HirakuError as exc:
            raise click.ClickException(str(exc)) from exc


@click.group(name="hiraku-agent", cls=AgentGroup)
def main() -> None:
    """Run Hiraku workflows from the terminal with a coding agent.

    `hiraku-agent WORKFLOW` starts a session, or resumes the active one, and
    lets the agent carry out instructions until completion or a pause.
    `hiraku-agent WORKFLOW EVENT` sends an event and continues. The agent runs
    headless (`claude -p` by default; set HIRAKU_AGENT_CMD or --agent-cmd), or
    in a conversation with you with --interactive (HIRAKU_AGENT_INTERACTIVE_CMD);
    exit the interactive agent without an event to pause the workflow.
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
    ]


def _workflow_command(workflow: Workflow, config: HirakuConfig) -> click.Group:
    arguments_model = workflow.graph.start.arguments
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
        options = _options(ctx, config)
        inputs = {
            name: values[name]
            for name in arguments_model.model_fields
            if values.get(name) is not None
        }
        arguments = (
            arguments_model.from_text(inputs).model_dump(
                mode="json", exclude_unset=True
            )
            if inputs
            else {}
        )
        session_id = _start_or_resume(workflow, options, arguments)
        ctx.exit(_drive(workflow, session_id, options))

    group = click.Group(
        name=workflow.id,
        callback=run,
        params=[*driver, new, *_model_params(arguments_model, driver + [new])],
        invoke_without_command=True,
        help=workflow.description,
        short_help=summary(workflow.description),
    )
    for method in workflow.graph.events.values():
        if method.source is None:
            continue
        group.add_command(_event_command(workflow, method, config))
    return group


def _event_command(
    workflow: Workflow, method: Event, config: HirakuConfig
) -> click.Command:
    """Return the subcommand that sends an event."""
    driver = _driver_params()
    name, model = method.name, method.arguments

    @click.pass_context
    def run(ctx: click.Context, **values: Any) -> None:
        options = _options(ctx, config)
        session = _select_for_event(workflow, name, options)
        result = session.run_event(
            workflow,
            session.instruction_id,
            name,
            model.from_text(
                {
                    name: values[name]
                    for name in model.model_fields
                    if values.get(name) is not None
                }
            ).model_dump(mode="json", exclude_unset=True),
        )
        result = result.model_dump(exclude_none=True)
        _report(
            session.instruction_id,
            _heading(session._instruction_response()),
            "you",
            name,
            result,
        )
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
        help=method.description,
        short_help=summary(method.description),
    )


def _unavailable(name: str, message: str) -> click.Command:
    def run() -> None:
        raise click.ClickException(f"Workflow {name!r} is unavailable: {message}")

    return click.Command(name=name, callback=run, short_help="(unavailable)")


def _options(ctx: click.Context, config: HirakuConfig) -> Options:
    """Merge the driver options of an event command with its workflow's."""
    merged: dict[str, Any] = {}
    for context in (ctx.parent, ctx):
        if context is None:
            continue
        for key in ("session", "new", "interactive", "agent_cmd"):
            value = context.params.get(key)
            if value not in (None, False):
                merged[key] = value
    return Options(config=config, **merged)


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


# Sessions ------------------------------------------------------------------------


def _active(workflow: Workflow, config: HirakuConfig) -> list[Session]:
    return [
        item
        for item in list_sessions(config=config)
        if item.workflow == workflow.id and item.status == "active"
    ]


def _own(workflow: Workflow, session_id: str, config: HirakuConfig) -> Session:
    session = load_session(session_id, config=config)
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
        return _own(workflow, options.session, options.config).id
    active = _active(workflow, options.config)
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
    response = Session.run_start(workflow, arguments).model_dump(exclude_none=True)
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
        return _own(workflow, options.session, options.config)
    active = _active(workflow, options.config)
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
    workflow: Workflow,
    session_id: str,
    options: Options,
) -> int:
    """Fetch assignments and submit event files after successful worker exits."""
    while True:
        session = load_session(session_id, config=options.config)
        response = session.get_instruction(workflow)
        if "instruction" not in response:
            raise click.ClickException(
                response.get("message", "No instruction available")
            )
        final = response["session_status"] == "completed"
        directory = _directory(options.config, response)
        completed = directory / "completed"
        if final and completed.is_file():
            context = response["instruction"].get("context")
            click.echo(f"completed: {json.dumps(context, ensure_ascii=False)}")
            return 0
        pending = directory / "pending.json"
        event_file = (
            pending
            if pending.exists() and not final
            else _run_agent(response, directory, options)
        )
        if final:
            completed.touch()
            context = response["instruction"].get("context")
            click.echo(f"completed: {json.dumps(context, ensure_ascii=False)}")
            return 0
        if event_file is None:
            if options.interactive:
                click.echo(
                    f"No event was submitted; instruction {response['instruction_id']} "
                    "is still pending. Resume with --interactive."
                )
                return EXIT_WAITING
            raise click.ClickException("Agent submitted no event")
        try:
            submission = _Submission.model_validate_json(event_file.read_text())
        except (OSError, PydanticValidationError) as exc:
            raise click.ClickException(
                f"Invalid event file {event_file}: {exc}"
            ) from exc
        result = session.run_event(
            workflow, response["instruction_id"], submission.event, submission.arguments
        ).model_dump(exclude_none=True)
        _report(
            response["instruction_id"],
            _heading(response),
            "agent",
            submission.event,
            result,
        )
        status = result["status"]
        if status == "not_ready":
            pending.write_text(submission.model_dump_json())
            click.echo("Waiting for evidence; check again later.")
            return EXIT_NOT_READY
        if pending.exists():
            pending.unlink()
        if status != "accepted":
            raise click.ClickException(
                f"{result.get('message', status)}; see {directory}"
            )


def _run_agent(
    response: dict[str, Any],
    directory: Path,
    options: Options,
) -> Path | None:
    final = response["session_status"] == "completed"
    directory.mkdir(parents=True, exist_ok=True)
    attempt = directory / str(
        1
        + max(
            (int(path.name) for path in directory.iterdir() if path.name.isdigit()),
            default=0,
        )
    )
    attempt.mkdir()
    prompt_file = attempt / "prompt.md"
    instruction_file = attempt / "instruction.json"
    event_file = attempt / "event.json"
    prompt_file.write_text(render_prompt(response, interactive=options.interactive))
    instruction_file.write_text(json.dumps(response, indent=2))
    custom = options.agent_cmd or os.environ.get(COMMAND_VARIABLES[options.interactive])
    command = shlex.split(custom or DEFAULT_COMMANDS[options.interactive])
    env = {
        **os.environ,
        "HIRAKU_SESSION_ID": response["session_id"],
        "HIRAKU_PROMPT_FILE": str(prompt_file),
        "HIRAKU_INSTRUCTION_FILE": str(instruction_file),
    }
    env.pop("HIRAKU_EVENT_FILE", None)
    env.pop("HIRAKU_MCP_CONFIG", None)
    if not final:
        env["HIRAKU_EVENT_FILE"] = str(event_file)
    if final:
        click.echo(f"[{response['instruction_id']} {_heading(response)}] final")
    try:
        if options.interactive:
            if not custom:
                command.append("--")
            completed = subprocess.run(
                [*command, prompt_file.read_text()],
                cwd=options.config.root,
                env=env,
                check=False,
            )
            code = completed.returncode
        else:
            with (
                prompt_file.open() as prompt,
                (attempt / "output.txt").open("w") as log,
            ):
                process = subprocess.Popen(
                    command,
                    stdin=prompt,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    cwd=options.config.root,
                    env=env,
                )
                assert process.stdout is not None
                for line in process.stdout:
                    click.echo(f"  │ {line.rstrip()}")
                    log.write(line)
                code = process.wait()
    except FileNotFoundError as exc:
        raise click.ClickException(
            f"Cannot run the agent command {command[0]!r}; "
            "install it or set --agent-cmd"
        ) from exc
    if code:
        raise click.ClickException(
            f"Agent exited with status {code}; no event was submitted. See {attempt}"
        )
    if final or not event_file.exists():
        return None
    return event_file


def _directory(config: HirakuConfig, response: dict[str, Any]) -> Path:
    return (
        config.sessions_dir / response["session_id"] / str(response["instruction_id"])
    )


def render_prompt(response: dict[str, Any], *, interactive: bool = False) -> str:
    """Render one assignment and its event-file output protocol."""
    instruction = Instruction.model_validate(response["instruction"])
    parts = [instruction.to_text()]
    if interactive:
        parts.append(
            "The user is in this conversation. Explain what you would do and wait "
            "for their go-ahead before acting."
        )
    else:
        parts.append("Carry out this assignment autonomously; do not ask questions.")
    if response["session_status"] == "completed":
        parts.append(
            "This is the final instruction. Carry it out, then stop. "
            "No event is needed."
        )
    else:
        parts.append(
            'Write one JSON object {"event": "EVENT_NAME", "arguments": {...}} '
            "to the file named by HIRAKU_EVENT_FILE, using one of the accepted "
            "events above, then stop. The parent will validate and submit it. "
            "Do not submit events through MCP or the CLI, or retrieve another "
            "assignment."
        )
    return "\n\n".join(parts) + "\n"


def _heading(response: dict[str, Any]) -> str:
    first = response["instruction"]["text"].splitlines()[0]
    return first.lstrip("#").strip()


def _report(
    instruction_id: int,
    heading: str,
    actor: str,
    name: str,
    result: dict[str, Any],
) -> None:
    message = f": {result.get('message', '')}" if result["status"] != "accepted" else ""
    click.echo(
        f"[{instruction_id} {heading}] {actor} → {name}  {result['status']}{message}"
    )


__all__ = ["DEFAULT_COMMANDS", "main", "render_prompt"]


if __name__ == "__main__":
    main()
