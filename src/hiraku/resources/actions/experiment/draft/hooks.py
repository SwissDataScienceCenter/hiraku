"""Validate the experiment draft and map its brief to record Markdown."""

from hiraku.agent.action import ActionError


def after(context, result):
    expected = {"id", "kind", "body"}
    if set(result.data) != expected:
        raise ActionError(
            "Experiment draft reply must contain exactly the documented fields"
        )
    if not isinstance(result.data.get("id"), str):
        raise ActionError("Experiment draft requires an id")
    if not isinstance(result.data["kind"], str) or result.data["kind"] not in {
        "baseline",
        "hypothesis",
        "diagnostic",
        "ablation",
        "combination",
    }:
        raise ActionError("Experiment kind is not supported")
    if not isinstance(result.data["body"], str) or not result.data["body"].strip():
        raise ActionError("Experiment draft body must be non-empty Markdown text")
    result.data["markdown"] = result.data.pop("body")
