"""Streaming adapters for external agent provider CLIs."""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from typing import TYPE_CHECKING, Any

from ..errors import ValidationError

if TYPE_CHECKING:
    from .action import ActionContext


def provider_command(
    provider: str, model: str | None, reasoning_effort: str | None = None
) -> list[str]:
    if provider == "codex":
        if reasoning_effort and reasoning_effort not in {
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
            "ultra",
        }:
            raise ValidationError(f"Invalid Codex reasoning effort: {reasoning_effort}")
        return [
            "codex",
            "exec",
            "--json",
            *(["--model", model] if model else []),
            *(
                ["-c", f'model_reasoning_effort="{reasoning_effort}"']
                if reasoning_effort
                else []
            ),
            "-",
        ]
    if provider == "claude":
        if reasoning_effort:
            raise ValidationError("Reasoning effort is only supported by Codex")
        return [
            "claude",
            "-p",
            "--verbose",
            "--output-format",
            "stream-json",
            *(["--model", model] if model else []),
        ]
    raise ValidationError(
        f"Unknown agent provider {provider!r}; choose codex or claude"
    )


def call_provider(
    provider: str,
    model: str | None,
    prompt: str,
    root: Path,
    reasoning_effort: str | None = None,
    *,
    context: ActionContext | None = None,
    report: Callable[..., None] | None = None,
) -> str:
    from .action import ActionError

    if report is None and context is not None:
        report = context.report
    command = provider_command(provider, model, reasoning_effort)
    if context and context.action.effect == "create":
        if provider == "codex":
            command[2:2] = ["--sandbox", "read-only"]
        else:
            command.extend(["--tools", ""])
    lines: Queue[str | None] = Queue()
    reply = ""
    diagnostics: list[str] = []

    def read_output(stream: Any) -> None:
        try:
            for line in stream:
                lines.put(line)
        finally:
            lines.put(None)

    try:
        with subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=root,
        ) as process:
            Thread(target=read_output, args=(process.stdout,), daemon=True).start()
            process.stdin.write(prompt)
            process.stdin.close()
            deadline = time.monotonic() + 1200
            try:
                while True:
                    if time.monotonic() >= deadline:
                        raise ActionError(f"{provider} timed out after 1200 seconds")
                    try:
                        line = lines.get(timeout=1)
                    except Empty:
                        continue
                    if line is None:
                        break
                    if context and context.log:
                        with (context.log / "provider.log").open("a") as stream:
                            stream.write(line)
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        diagnostics.append(line)
                        if report:
                            report("provider_output", line.strip())
                        continue
                    if not isinstance(event, dict):
                        continue
                    kind = event.get("type")
                    item = event.get("item", {})
                    if kind == "item.completed" and item.get("type") == "agent_message":
                        reply = item.get("text", "")
                    elif kind == "result":
                        if event.get("is_error"):
                            raise ActionError(str(event.get("result", event)))
                        reply = event.get("result", "")
                    elif kind in {"error", "turn.failed"}:
                        diagnostics.append(json.dumps(event))
                    if report:
                        if kind == "item.started" and item.get("command"):
                            report("command", item["command"])
                        elif (
                            kind == "item.completed"
                            and item.get("type") == "file_change"
                        ):
                            for change in item.get("changes", []):
                                report("file_changed", change.get("path", ""))
                        elif kind == "assistant":
                            for content in event.get("message", {}).get("content", []):
                                if content.get("type") == "tool_use":
                                    arguments = content.get("input", {})
                                    detail = (
                                        arguments.get("file_path")
                                        or arguments.get("command")
                                        or ""
                                    )
                                    report(
                                        "tool",
                                        f"{content.get('name', 'tool')}: {detail}",
                                        input=arguments,
                                    )
                        elif kind == "turn.started":
                            report("provider_progress", "Agent started working")
            except BaseException:
                process.kill()
                process.wait()
                raise
            process.wait(timeout=max(1, deadline - time.monotonic()))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ActionError(f"Could not run {provider}: {exc}") from exc
    if process.returncode:
        raise ActionError(
            f"{provider} exited {process.returncode}: {''.join(diagnostics)[-1000:]}"
        )
    if not reply.strip():
        raise ActionError(f"{provider} returned no output")
    return reply
