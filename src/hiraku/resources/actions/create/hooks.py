"""Validate creation output and map the brief to record Markdown."""

from hiraku.agent.action import ActionError


def after(context, result):
    if context.supplied_id is not None:
        result.data["id"] = context.supplied_id
    if context.record_type == "task":
        _task(context, result)
    elif context.record_type == "experiment":
        _experiment(context, result)


def _task(context, result):
    expected = {"id", "goal", "question", "summary", "body", "metrics", "notes"}
    if set(result.data) != expected:
        raise ActionError(
            "Task create reply must contain exactly the documented fields"
        )
    for field in ("id", "goal", "question", "summary"):
        if not isinstance(result.data[field], str):
            raise ActionError(f"Task create field {field!r} must be a string")
    if result.data["body"] is not None and not isinstance(result.data["body"], str):
        raise ActionError("Task create body must be Markdown text or null")
    if not isinstance(result.data["metrics"], list):
        raise ActionError("Task create metrics must be a list")
    notes = result.data.pop("notes")
    if notes:
        context.report("create_notes", str(notes))
    result.data["markdown"] = result.data.pop("body") or ""


def _experiment(context, result):
    expected = {"id", "kind", "body"}
    if set(result.data) != expected:
        raise ActionError(
            "Experiment create reply must contain exactly the documented fields"
        )
    if not isinstance(result.data.get("id"), str):
        raise ActionError("Experiment create requires an id")
    if not isinstance(result.data["kind"], str) or result.data["kind"] not in {
        "baseline",
        "hypothesis",
        "diagnostic",
        "ablation",
        "combination",
    }:
        raise ActionError("Experiment kind is not supported")
    if not isinstance(result.data["body"], str) or not result.data["body"].strip():
        raise ActionError("Experiment create body must be non-empty Markdown text")
    result.data["markdown"] = result.data.pop("body")
