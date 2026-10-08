"""MCP tools for persisting literature evidence."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer

from ...core.agent import Context, list_contexts
from ...core.config import HirakuConfig
from ...core.errors import ValidationError
from ...core.record import load_record
from ..schemas import LiteratureReview


def register(
    server: MCPServer, get_config: Callable[[], HirakuConfig]
) -> dict[str, Callable[..., Any]]:
    """Register the literature review persistence tool."""

    @server.tool()
    def save_literature_review(
        task_id: str, review: LiteratureReview
    ) -> dict[str, Any]:
        """Persist a provenance-aware literature review as task-linked context."""
        config = get_config()
        task = load_record(task_id, config=config)
        if task.type != "task":
            raise ValidationError(f"Record {task_id!r} is not a task")
        if any(
            context.id == review.context_id for context in list_contexts(config=config)
        ):
            raise ValidationError(f"Context {review.context_id!r} already exists")

        source_sections = []
        for source in review.sources:
            citation = source.title
            if source.authors:
                citation = f"{', '.join(source.authors)}. {citation}"
            if source.year:
                citation += f" ({source.year})"
            if source.identifier:
                citation += f". {source.identifier}"
            if source.url:
                citation += f". {source.url}"
            source_sections.append(
                f"### {citation}\n\n"
                f"Verification status: `{source.verification_status}`\n\n"
                f"{source.evidence_notes or 'No source-specific notes recorded.'}"
            )
        directions = (
            "\n".join(
                f"{index}. {direction}"
                for index, direction in enumerate(review.candidate_directions, start=1)
            )
            or "No candidate directions recorded."
        )
        sources_markdown = "\n\n".join(source_sections)
        markdown = (
            f"# Literature review: {review.scope}\n\n"
            f"## Synthesis\n\n{review.synthesis}\n\n"
            f"## Candidate directions\n\n{directions}\n\n"
            f"## Limitations\n\n{review.limitations}\n\n"
            f"## Sources and verification\n\n{sources_markdown}"
        )
        context = Context(
            id=review.context_id,
            task_id=task_id,
            kind="literature-review",
            sources=[source.model_dump() for source in review.sources],
            markdown=markdown,
        )
        path = config.contexts_dir / review.context_id / "context.md"
        context.save(path)
        return {"id": context.id, "task_id": task_id, "path": str(path)}

    return {"save_literature_review": save_literature_review}
