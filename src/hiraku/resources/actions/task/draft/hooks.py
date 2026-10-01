"""Validate and map the task drafting prompt's output."""

from hiraku.agent.action import ActionError


def after(context, result):
    expected = {"id", "goal", "question", "summary", "body", "metrics", "notes"}
    if set(result.data) != expected:
        raise ActionError("Task draft reply must contain exactly the documented fields")
    for field in ("id", "goal", "question", "summary"):
        if not isinstance(result.data[field], str):
            raise ActionError(f"Task draft field {field!r} must be a string")
    if result.data["body"] is not None and not isinstance(result.data["body"], str):
        raise ActionError("Task draft body must be Markdown text or null")
    if not isinstance(result.data["metrics"], list):
        raise ActionError("Task draft metrics must be a list")
    notes = result.data.pop("notes")
    if notes:
        context.report("draft_notes", str(notes))
    result.data["markdown"] = result.data.pop("body") or ""
