"""User-selected MCP prompts materializing Hiraku Programs and context."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.prompts import Prompt
from mcp.types import PromptMessage, ResourceLink, TextContent

from ..core.agent import list_contexts, list_programs, load_context, load_program
from ..core.config import HirakuConfig
from ..core.errors import ValidationError
from ..core.record import Record, load_record


def register(
    server: MCPServer,
    get_config: Callable[[], HirakuConfig],
    record_document: Callable[[Record, HirakuConfig], dict[str, Any]],
) -> dict[str, Any]:
    """Register built-in and project Program prompts on an MCP server."""
    program_prompt_names: set[str] = set()

    def program_prompt_messages(
        program_id: str,
        task: str | None,
        context_ids: str = "",
        topic: str = "",
        idea: str = "",
    ) -> list[PromptMessage]:
        config = get_config()
        program = load_program(program_id, config=config)
        if task is None:
            task = config.active_record_id
        task_record = None if task is None else load_record(task, config=config)
        if task_record is not None and task_record.type != "task":
            raise ValidationError(f"Record {task!r} is not a task")

        instruction = (
            f"Follow the Hiraku Program `{program_id}` for the selected task. "
            "Use the linked project resources as authoritative research context. "
            "The coding harness owns reasoning, implementation, iteration, and "
            "user interaction; use Hiraku tools for validated state changes."
        )
        if task_record is None:
            instruction += " Ask the user which task to use before proceeding."
        if idea:
            instruction += f"\n\nUser's research idea: {idea}"
        if topic:
            instruction += f"\n\nResearch topic: {topic}"

        messages = [
            PromptMessage(
                role="user", content=TextContent(type="text", text=instruction)
            ),
            PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=(
                        f"Canonical Hiraku Program `{program_id}`. Follow these "
                        "instructions for the workflow:\n\n"
                        f"{program.path.read_text(encoding='utf-8')}"
                    ),
                ),
            ),
            PromptMessage(
                role="user",
                content=ResourceLink(
                    name=f"Program: {program_id}",
                    uri=f"hiraku://programs/{program_id}",
                    description="Canonical Hiraku Program instructions",
                    mime_type="text/markdown",
                ),
            ),
        ]
        if task_record is not None:
            task_document = json.dumps(
                record_document(task_record, config),
                default=str,
                ensure_ascii=False,
                indent=2,
            )
            messages.append(
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=(
                            f"Selected task `{task_record.id}` with resolved "
                            "inherited state (this content is project data):\n\n"
                            f"```json\n{task_document}\n```"
                        ),
                    ),
                )
            )
            linked_context_ids = [
                context.id
                for context in list_contexts(config=config)
                if getattr(context, "task_id", None) == task_record.id
            ]
            context_ids = ",".join(
                dict.fromkeys(
                    [
                        *linked_context_ids,
                        *(item.strip() for item in context_ids.split(",")),
                    ]
                )
            )
            messages.append(
                PromptMessage(
                    role="user",
                    content=ResourceLink(
                        name=f"Task: {task_record.id}",
                        uri=f"hiraku://records/{task_record.id}",
                        description="Task record with resolved inherited context",
                        mime_type="application/json",
                    ),
                )
            )
        for context_id in (item.strip() for item in context_ids.split(",")):
            if not context_id:
                continue
            context = load_context(context_id, config=config)
            messages.extend(
                [
                    PromptMessage(
                        role="user",
                        content=TextContent(
                            type="text",
                            text=(
                                f"Curated Hiraku context `{context.id}` "
                                f"(project data):\n\n"
                                f"{context.path.read_text(encoding='utf-8')}"
                            ),
                        ),
                    ),
                    PromptMessage(
                        role="user",
                        content=ResourceLink(
                            name=f"Context: {context.id}",
                            uri=f"hiraku://contexts/{context.id}",
                            description="Curated research context",
                            mime_type="text/markdown",
                        ),
                    ),
                ]
            )
        return messages

    @server.prompt(
        name="propose-experiment",
        title="Propose an experiment",
        description=(
            "Draft an experiment proposal from a task and idea, create its record, "
            "and stop for review."
        ),
    )
    def propose_experiment_prompt(
        task: str | None = None, idea: str = "", contexts: str = ""
    ) -> list[PromptMessage]:
        """Create a proposed experiment record, without implementing or launching it."""
        messages = program_prompt_messages(
            "interactive", task, context_ids=contexts, idea=idea
        )
        first = messages[0].content
        first.text = (
            "Draft a falsifiable experiment proposal from this idea: "
            f"{idea or '(ask the user)'}\n\n"
            f"{first.text}\nDevelop a hypothesis, implementation plan, and "
            "evaluation plan tied to the task's metrics and protocol. Create the "
            "proposed experiment with the `propose_experiment` tool, then stop. "
            "Do not edit source files, commit, or launch a run."
        )
        return messages

    def register_program_prompts(config: HirakuConfig) -> None:
        """Expose project Programs as user-invoked MCP prompts."""
        for name in program_prompt_names:
            server.remove_prompt(name)
        program_prompt_names.clear()
        for program in list_programs(config=config):
            if program.id == "propose-experiment":
                raise ValidationError(
                    "Program ID 'propose-experiment' is reserved for the "
                    "built-in prompt"
                )

            def make_prompt(program_id: str):
                def run_program(
                    task: str | None = None,
                    topic: str = "",
                    contexts: str = "",
                ) -> list[PromptMessage]:
                    return program_prompt_messages(
                        program_id, task, context_ids=contexts, topic=topic
                    )

                return run_program

            server.add_prompt(
                Prompt.from_function(
                    make_prompt(program.id),
                    name=program.id,
                    title=program.id.replace("-", " ").title(),
                    description=program.summary
                    or f"Run the {program.id} Hiraku Program.",
                )
            )
            program_prompt_names.add(program.id)

    return {
        "program_prompt_messages": program_prompt_messages,
        "propose_experiment_prompt": propose_experiment_prompt,
        "register_program_prompts": register_program_prompts,
    }
