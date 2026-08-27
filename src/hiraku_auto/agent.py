"""Calling Claude Code (or any other agent CLI) and applying what it returns.

The contract in both directions is JSON:

    project state  ->  prompt  ->  `claude -p`  ->  one JSON object  ->  this module writes records

The agent decides; the tool writes. That split is the point. It keeps file writes deterministic and
reviewable, stops the agent wandering outside the workflow, and means a bad suggestion is a diff you
can revert rather than an edit you have to find.

Default command is `claude -p <prompt>`, which is the one form available in restricted
environments. Override with --agent-cmd or HIRAKU_AGENT_CMD (e.g. "codex exec", "claude -p
--output-format text").
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import context as context_mod
from .config import Project
from .errors import AgentError, HirakuError
from .prompts import PROMPT_DIR
from .records import (
    load_experiment,
    load_goal,
    save_experiment,
    validate_id,
)
from .runs import best
from .scaffold import apply_goal_draft, create_experiment, create_proposal, today
from .tree import Forest, build, to_dict

DEFAULT_AGENT_CMD = "claude -p"
DEFAULT_TIMEOUT = 900

WORKFLOWS = ("draft", "design", "triage", "discover", "analyze")

# Where reading the project's own files earns its place in the prompt. `draft` and `design` are
# writing the terms the rest of the loop inherits, and `discover` is judging whether an outside
# idea applies here — all three are worse without the project's own definitions. `triage` and
# `analyze` reason about runs that already happened, where the run table is the evidence and extra
# prose is dead weight; pass --context there if a particular file is genuinely needed.
CONTEXT_BY_DEFAULT = ("draft", "design", "discover")


@dataclass
class AgentRun:
    workflow: str
    prompt: str
    raw: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    written: list[Path] = field(default_factory=list)
    context: context_mod.Bundle = field(default_factory=context_mod.Bundle)
    log: Path | None = None
    seconds: float = 0.0


@dataclass
class Hooks:
    """Where the caller gets told what is happening.

    An agent call is a minutes-long subprocess that prints nothing until it finishes. Without
    these the terminal sits blank and the only honest thing a researcher can conclude is that
    something has hung.
    """

    start: Callable[[dict[str, Any]], None] | None = None
    tick: Callable[[float], None] | None = None
    done: Callable[[dict[str, Any]], None] | None = None


# --------------------------------------------------------------------------- prompt assembly


def agent_command(override: str | None = None) -> list[str]:
    raw = override or os.environ.get("HIRAKU_AGENT_CMD") or DEFAULT_AGENT_CMD
    parts = shlex.split(raw)
    if not parts:
        raise AgentError("Agent command is empty.")
    return parts


def gather_context(
    project: Project,
    workflow: str,
    *,
    use: bool | None = None,
    extra: list[str] | None = None,
    max_bytes: int | None = None,
) -> context_mod.Bundle:
    """The project's own files, for the workflows that are better for having read them.

    `use=None` means "whatever this workflow does by default"; True and False are the explicit
    `--context` / `--no-context` answers and always win.
    """
    enabled = (workflow in CONTEXT_BY_DEFAULT) if use is None else use
    if extra and use is not False:
        enabled = True
    return context_mod.collect(
        project, extra=list(extra or []), enabled=enabled, max_bytes=max_bytes
    )


def attach_context(state: dict[str, Any], bundle: context_mod.Bundle) -> dict[str, Any]:
    if bundle:
        state["project_context"] = bundle.to_state()
    return state


def build_prompt(
    workflow: str,
    state: dict[str, Any],
    *,
    instruction: str = "",
    project: Project | None = None,
) -> str:
    if workflow not in WORKFLOWS:
        raise HirakuError(f"Unknown workflow '{workflow}'. One of: {', '.join(WORKFLOWS)}")
    template_path = PROMPT_DIR / f"{workflow}.md"
    local = None
    if project is not None:
        candidate = project.root / ".agents" / "skills" / "hiraku-auto" / f"{workflow}.md"
        if candidate.is_file():
            local = candidate
    text = (local or template_path).read_text(encoding="utf-8")

    rules_path = PROMPT_DIR / "_rules.md"
    if project is not None:
        candidate = project.root / ".agents" / "skills" / "hiraku-auto" / "_rules.md"
        if candidate.is_file():
            rules_path = candidate
    rules = rules_path.read_text(encoding="utf-8")

    return (
        text.replace("{{rules}}", rules)
        .replace("{{state}}", json.dumps(state, indent=2, default=str))
        .replace("{{instruction}}", instruction or "")
    )


def slim_state(
    forest: Forest, *, goal: str | None = None, focus: str | None = None
) -> dict[str, Any]:
    """The state the agent sees. Scoped to one goal where possible, so the prompt stays small and
    the agent is not tempted to reason across incomparable protocols."""
    full = to_dict(forest)
    if goal is None and focus and focus in forest.nodes:
        goal = forest.nodes[focus].goal
    if goal is None:
        return full

    goal_record = forest.goals.get(goal)
    node_ids = set(forest.subtree_all(goal)) | {
        nid for nid, node in forest.nodes.items() if node.goal == goal
    }
    project_front = {}
    if forest.project.config_md.is_file():
        from .records import read_record

        project_front = read_record(forest.project.config_md).front

    return {
        "project_bounds": project_front.get("bounds", {}),
        "project_promotion": project_front.get("promotion", {}),
        "goal": {
            "id": goal,
            "front": goal_record.front if goal_record else {},
            "sections": goal_record.sections if goal_record else {},
        },
        "focus_experiment": focus,
        "experiments": {nid: full["experiments"][nid] for nid in sorted(node_ids)},
        "proposals": {
            pid: p
            for pid, p in full["proposals"].items()
            if p["front"].get("goal") in (goal, None)
        },
        "runs_for_goal": [
            r for r in full["runs"] if r.get("experiment_id") in node_ids
        ],
        "issues": full["issues"],
    }


# --------------------------------------------------------------------------- invocation


def extract_json(text: str) -> dict[str, Any]:
    """Pull one JSON object out of an agent's reply, fenced or bare."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = [fenced.group(1)] if fenced else []
    # Balanced-brace scan from the first '{' as a fallback.
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : index + 1])
                    break
        break
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            # `claude --output-format json` wraps the reply; unwrap it.
            if "result" in parsed and isinstance(parsed["result"], str):
                return extract_json(parsed["result"])
            return parsed
    raise AgentError(
        "The agent did not return a JSON object. First 500 characters of the reply:\n"
        + text[:500]
    )


def call_agent(
    prompt: str,
    *,
    command: list[str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    cwd: Path | None = None,
    progress: Callable[[float], None] | None = None,
) -> str:
    """Run the agent, calling `progress(elapsed_seconds)` while waiting.

    Popen rather than `subprocess.run` so the wait is interruptible: `run` blocks until the
    process exits, which is the whole reason nothing appeared on the terminal for minutes. The
    pipes are drained on their own threads, because a reply large enough to fill the OS pipe
    buffer would otherwise deadlock the child against a parent that is only polling.
    """
    argv = command or agent_command()
    if shutil.which(argv[0]) is None:
        raise AgentError(
            f"Agent command '{argv[0]}' not found on PATH. Install Claude Code, or pass "
            f"--agent-cmd, or use --dry-run to print the prompt and run it yourself."
        )
    process = subprocess.Popen(  # noqa: S603
        [*argv, prompt],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(cwd) if cwd else None,
    )
    buffers: dict[str, str] = {"out": "", "err": ""}

    def drain(stream: Any, key: str) -> None:
        buffers[key] = stream.read() or ""

    readers = [
        threading.Thread(target=drain, args=(process.stdout, "out"), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, "err"), daemon=True),
    ]
    for reader in readers:
        reader.start()

    started = time.monotonic()
    while True:
        try:
            process.wait(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - started
            if elapsed > timeout:
                process.kill()
                process.wait()
                raise AgentError(
                    f"Agent timed out after {timeout}s. Raise it with --timeout, or run the "
                    f"prompt yourself: the exact text is in the log directory."
                ) from None
            if progress:
                progress(elapsed)
    for reader in readers:
        reader.join(timeout=5)

    if process.returncode != 0:
        raise AgentError(
            f"Agent exited {process.returncode}: {(buffers['err'] or buffers['out'])[:500]}"
        )
    if not buffers["out"].strip():
        raise AgentError("Agent produced no output.")
    return buffers["out"]


# --------------------------------------------------------------------------- logging


LOG_DIRNAME = "agent-logs"


def logs_root(project: Project) -> Path:
    return project.root / ".hiraku" / LOG_DIRNAME


def _open_log(project: Project, workflow: str, target: str) -> Path:
    stamp = _dt.datetime.now().strftime("%Y-%m-%dT%H%M%S")
    directory = logs_root(project) / f"{stamp}-{workflow}-{target}"
    suffix = 1
    while directory.exists():
        suffix += 1
        directory = logs_root(project) / f"{stamp}-{workflow}-{target}-{suffix}"
    directory.mkdir(parents=True)
    return directory


def read_logs(project: Project, limit: int = 20) -> list[dict[str, Any]]:
    """The most recent calls, newest first. Each entry is what `meta.json` recorded."""
    root = logs_root(project)
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for directory in sorted(root.iterdir(), reverse=True):
        if not directory.is_dir():
            continue
        meta_path = directory / "meta.json"
        meta: dict[str, Any] = {"id": directory.name, "path": directory}
        if meta_path.is_file():
            try:
                meta.update(json.loads(meta_path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                meta["error"] = "meta.json is not readable"
        else:
            meta["error"] = "still running, or the process was killed"
        meta["id"], meta["path"] = directory.name, directory
        out.append(meta)
        if len(out) >= limit:
            break
    return out


def _invoke(
    project: Project,
    workflow: str,
    target: str,
    prompt: str,
    bundle: context_mod.Bundle,
    *,
    command: list[str] | None,
    timeout: int,
    hooks: Hooks | None,
) -> tuple[str, Path, float]:
    """One agent call: announced before it starts, ticking while it waits, logged either way."""
    argv = command or agent_command()
    directory = _open_log(project, workflow, target)
    (directory / "prompt.md").write_text(prompt, encoding="utf-8")

    info: dict[str, Any] = {
        "workflow": workflow,
        "target": target,
        "command": " ".join(argv),
        "prompt_chars": len(prompt),
        "context": bundle.summary(),
        "log": project.rel(directory),
        "started": _dt.datetime.now().isoformat(timespec="seconds"),
        "timeout": timeout,
    }

    def write_meta(extra: dict[str, Any]) -> None:
        (directory / "meta.json").write_text(
            json.dumps({**info, **extra}, indent=2, default=str), encoding="utf-8"
        )

    if hooks and hooks.start:
        hooks.start(info)
    started = time.monotonic()
    try:
        raw = call_agent(
            prompt,
            command=argv,
            timeout=timeout,
            cwd=project.root,
            progress=hooks.tick if hooks else None,
        )
    except Exception as exc:
        write_meta({"seconds": round(time.monotonic() - started, 1), "error": str(exc)})
        if hooks and hooks.done:
            hooks.done({**info, "seconds": round(time.monotonic() - started, 1), "error": str(exc)})
        raise
    seconds = round(time.monotonic() - started, 1)
    (directory / "reply.txt").write_text(raw, encoding="utf-8")
    write_meta({"seconds": seconds, "reply_chars": len(raw), "error": None})
    if hooks and hooks.done:
        hooks.done({**info, "seconds": seconds, "error": None})
    return raw, directory, seconds


# --------------------------------------------------------------------------- workflows


def _require(data: dict[str, Any], key: str, kind: type) -> Any:
    value = data.get(key)
    if not isinstance(value, kind):
        raise AgentError(f"Agent reply is missing a valid '{key}' ({kind.__name__}).")
    return value


def run_draft(
    project: Project,
    goal_id: str,
    question: str,
    *,
    instruction: str = "",
    command: list[str] | None = None,
    dry_run: bool = False,
    apply: bool = True,
    revise: bool = False,
    with_experiments: bool = False,
    use_context: bool | None = None,
    extra_context: list[str] | None = None,
    context_bytes: int | None = None,
    hooks: Hooks | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> AgentRun:
    """Turn a question in the researcher's own words into a goal record they can review.

    This is the step that used to be a blank template. The agent reads the project's own files,
    writes the metric, the success criterion and the five-part evaluation protocol, and marks with
    `**[UNSET: ...]**` every value it could not ground — so what reaches the researcher is a draft
    to correct rather than a form to fill.
    """
    validate_id(goal_id, "Goal ID")
    if not question.strip():
        raise HirakuError("A question is required: pass --question, or --question-file.")

    forest = build(project)
    bundle = gather_context(
        project, "draft", use=use_context, extra=extra_context, max_bytes=context_bytes
    )
    existing = forest.goals.get(goal_id)
    state = attach_context(
        {
            "task": "draft a goal record",
            "goal_id": goal_id,
            "researcher_question": question.strip(),
            "existing_goal": (
                {"front": existing.front, "sections": existing.sections} if existing else None
            ),
            "other_goals": {
                gid: {
                    "primary_metric": record.front.get("primary_metric"),
                    "question": record.section("Question")[:400],
                }
                for gid, record in forest.goals.items()
                if gid != goal_id
            },
            "project_bounds": _project_front(project).get("bounds", {}),
        },
        bundle,
    )
    prompt = build_prompt("draft", state, instruction=instruction, project=project)
    result = AgentRun(workflow="draft", prompt=prompt, context=bundle)
    if dry_run:
        return result

    result.raw, result.log, result.seconds = _invoke(
        project, "draft", goal_id, prompt, bundle, command=command, timeout=timeout, hooks=hooks
    )
    result.data = extract_json(result.raw)
    if not result.data.get("primary_metric"):
        raise AgentError("Agent reply is missing a valid 'primary_metric' (str).")
    if not apply:
        return result

    result.written.append(apply_goal_draft(project, goal_id, result.data, revise=revise))

    if with_experiments:
        for item in result.data.get("first_experiments") or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            path = create_experiment(
                project,
                str(item["id"]),
                goal=goal_id,
                kind=str(item.get("kind") or "hypothesis"),
                status="proposed",
                origin="agent-diagnostic",
                expectation=str(item.get("expectation") or ""),
                scope=str(item.get("scope") or ""),
            )
            record = load_experiment(project, str(item["id"]))
            if item.get("rationale"):
                record.append_section(
                    "Decisions and next steps", f"**Why this node exists.** {item['rationale']}"
                )
            save_experiment(record)
            result.written.append(path)
    return result


def _project_front(project: Project) -> dict[str, Any]:
    if not project.config_md.is_file():
        return {}
    from .records import read_record

    return read_record(project.config_md).front


def run_design(
    project: Project,
    goal_id: str,
    *,
    instruction: str = "",
    command: list[str] | None = None,
    dry_run: bool = False,
    apply: bool = True,
    use_context: bool | None = None,
    extra_context: list[str] | None = None,
    context_bytes: int | None = None,
    hooks: Hooks | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> AgentRun:
    forest = build(project)
    load_goal(project, goal_id)
    bundle = gather_context(
        project, "design", use=use_context, extra=extra_context, max_bytes=context_bytes
    )
    prompt = build_prompt(
        "design",
        attach_context(slim_state(forest, goal=goal_id), bundle),
        instruction=instruction,
        project=project,
    )
    result = AgentRun(workflow="design", prompt=prompt, context=bundle)
    if dry_run:
        return result

    result.raw, result.log, result.seconds = _invoke(
        project, "design", goal_id, prompt, bundle, command=command, timeout=timeout, hooks=hooks
    )
    result.data = extract_json(result.raw)
    proposed = _require(result.data, "experiments", list)
    if not apply:
        return result

    for item in proposed:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        path = create_experiment(
            project,
            str(item["id"]),
            goal=goal_id,
            parent=str(item["parent"]) if item.get("parent") else None,
            kind=str(item.get("kind") or "hypothesis"),
            status="proposed",
            origin="agent-diagnostic",
            expectation=str(item.get("expectation") or ""),
            scope=str(item.get("scope") or ""),
        )
        record = load_experiment(project, str(item["id"]))
        if item.get("expectation_body"):
            record.set_section("Expectation and how it could fail", str(item["expectation_body"]))
        if item.get("rationale"):
            record.append_section(
                "Decisions and next steps", f"**Why this node exists.** {item['rationale']}"
            )
        save_experiment(record)
        result.written.append(path)
    return result


def run_triage(
    project: Project,
    experiment_id: str,
    *,
    instruction: str = "",
    command: list[str] | None = None,
    dry_run: bool = False,
    apply: bool = True,
    use_context: bool | None = None,
    extra_context: list[str] | None = None,
    context_bytes: int | None = None,
    hooks: Hooks | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> AgentRun:
    forest = build(project)
    if experiment_id not in forest.nodes:
        raise HirakuError(f"Experiment '{experiment_id}' does not exist.")
    node = forest.nodes[experiment_id]
    bundle = gather_context(
        project, "triage", use=use_context, extra=extra_context, max_bytes=context_bytes
    )
    prompt = build_prompt(
        "triage",
        attach_context(slim_state(forest, goal=node.goal, focus=experiment_id), bundle),
        instruction=instruction,
        project=project,
    )
    result = AgentRun(workflow="triage", prompt=prompt, context=bundle)
    if dry_run:
        return result

    result.raw, result.log, result.seconds = _invoke(
        project, "triage", experiment_id, prompt, bundle,
        command=command, timeout=timeout, hooks=hooks,
    )
    result.data = extract_json(result.raw)
    data = result.data
    if data.get("blocked"):
        return result
    if not apply:
        return result

    record = load_experiment(project, experiment_id)
    if data.get("findings"):
        record.set_section("Findings", str(data["findings"]))
    if data.get("conclusions"):
        record.set_section("Conclusions", str(data["conclusions"]))

    failure_class = str(data.get("failure_class") or "")
    verdict = data.get("verdict")
    if failure_class in {"B", "C"} and verdict in {"refutes", "inconclusive"}:
        record.front["status"] = "concluded"
        record.front["verdict"] = verdict
        record.front["concluded"] = today()

    test = data.get("investigation_test") or {}
    if isinstance(test, dict) and test:
        lines = ["**Investigation test.**", ""]
        for key, value in test.items():
            if not isinstance(value, dict):
                continue
            mark = "pass" if value.get("pass") else "FAIL"
            lines.append(f"- `{key}` — {mark}. {value.get('note', '')}".rstrip())
        if not data.get("proceed"):
            lines += ["", f"Stopped: condition `{data.get('blocking_condition')}` failed."]
        record.append_section("Decisions and next steps", "\n".join(lines))
    save_experiment(record)
    result.written.append(record.path)

    if data.get("proceed"):
        for child in data.get("children") or []:
            if not isinstance(child, dict) or not child.get("id"):
                continue
            path = create_experiment(
                project,
                str(child["id"]),
                parent=experiment_id,
                kind=str(child.get("kind") or "diagnostic"),
                mode=str(record.front.get("mode") or "guided"),
                status="proposed",
                origin="agent-diagnostic",
                expectation=str(child.get("expectation") or ""),
                scope=str(child.get("scope") or ""),
            )
            created = load_experiment(project, str(child["id"]))
            if child.get("expectation_body"):
                created.set_section(
                    "Expectation and how it could fail", str(child["expectation_body"])
                )
            save_experiment(created)
            result.written.append(path)
    return result


def run_discover(
    project: Project,
    goal_id: str,
    *,
    instruction: str = "",
    command: list[str] | None = None,
    dry_run: bool = False,
    apply: bool = True,
    use_context: bool | None = None,
    extra_context: list[str] | None = None,
    context_bytes: int | None = None,
    hooks: Hooks | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> AgentRun:
    forest = build(project)
    load_goal(project, goal_id)
    bundle = gather_context(
        project, "discover", use=use_context, extra=extra_context, max_bytes=context_bytes
    )
    prompt = build_prompt(
        "discover",
        attach_context(slim_state(forest, goal=goal_id), bundle),
        instruction=instruction,
        project=project,
    )
    result = AgentRun(workflow="discover", prompt=prompt, context=bundle)
    if dry_run:
        return result

    result.raw, result.log, result.seconds = _invoke(
        project, "discover", goal_id, prompt, bundle, command=command, timeout=timeout, hooks=hooks
    )
    result.data = extract_json(result.raw)
    proposals = _require(result.data, "proposals", list)
    if not apply:
        return result

    for item in proposals:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        path = create_proposal(
            project,
            str(item["id"]),
            goal=goal_id,
            origin="agent-literature",
            proposed_parent=str(item["proposed_parent"]) if item.get("proposed_parent") else None,
            kind=str(item.get("kind") or "hypothesis"),
            title=str(item.get("title") or ""),
            hypothesis=str(item.get("hypothesis") or ""),
            provenance=str(item.get("provenance") or ""),
            applicability=str(item.get("applicability") or ""),
            prediction=str(item.get("prediction") or ""),
            investigation_test=str(item.get("investigation_test") or ""),
            cost=str(item.get("cost") or ""),
            counterargument=str(item.get("counterargument") or ""),
        )
        result.written.append(path)
    return result


def run_analyze(
    project: Project,
    experiment_id: str,
    *,
    instruction: str = "",
    command: list[str] | None = None,
    dry_run: bool = False,
    apply: bool = True,
    use_context: bool | None = None,
    extra_context: list[str] | None = None,
    context_bytes: int | None = None,
    hooks: Hooks | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> AgentRun:
    forest = build(project)
    if experiment_id not in forest.nodes:
        raise HirakuError(f"Experiment '{experiment_id}' does not exist.")
    node = forest.nodes[experiment_id]
    if not node.runs:
        raise HirakuError(f"'{experiment_id}' has no runs to analyse.")
    bundle = gather_context(
        project, "analyze", use=use_context, extra=extra_context, max_bytes=context_bytes
    )
    prompt = build_prompt(
        "analyze",
        attach_context(slim_state(forest, goal=node.goal, focus=experiment_id), bundle),
        instruction=instruction,
        project=project,
    )
    result = AgentRun(workflow="analyze", prompt=prompt, context=bundle)
    if dry_run:
        return result

    result.raw, result.log, result.seconds = _invoke(
        project, "analyze", experiment_id, prompt, bundle,
        command=command, timeout=timeout, hooks=hooks,
    )
    result.data = extract_json(result.raw)
    if not apply:
        return result

    record = load_experiment(project, experiment_id)
    for key, section in (
        ("findings", "Findings"),
        ("conclusions", "Conclusions"),
        ("next_steps", "Decisions and next steps"),
    ):
        if result.data.get(key):
            record.set_section(section, str(result.data[key]))
    verdict = result.data.get("verdict")
    if verdict in {"supports", "refutes", "inconclusive"}:
        record.front["status"] = "concluded"
        record.front["verdict"] = verdict
        record.front["concluded"] = today()
    candidate = result.data.get("promotable_candidate")
    if candidate:
        record.append_section(
            "Decisions and next steps",
            f"Promotion candidate: run `{candidate}` — run `hiraku-auto promote "
            f"{experiment_id} --run {candidate}` to put it through the gate.",
        )
    save_experiment(record)
    result.written.append(record.path)
    return result


def suggest_next(forest: Forest, goal_id: str) -> dict[str, Any]:
    """What the loop would do next, computed without the agent.

    Deterministic on purpose: the ordering of work is policy, not judgement, so it stays in code
    where it can be read and tested.
    """
    if goal_id not in forest.goals:
        raise HirakuError(f"Goal '{goal_id}' does not exist.")
    nodes = [forest.nodes[nid] for nid in forest.subtree_all(goal_id)]
    goal = forest.goals.get(goal_id)

    # An empty goal is not an exhausted one. Distinguishing them matters: "exhausted" is a
    # finding, "empty" is a prompt to design the tree.
    if not nodes:
        return {
            "action": "design",
            "why": "the goal has no experiments yet",
            "next_commands": [
                f"hiraku-auto agent design {goal_id}    # let the agent propose the tree",
                f"hiraku-auto agent discover {goal_id}  # or search the literature first",
                "hiraku-auto exp create <id> --goal "
                f"{goal_id} --kind baseline --expectation \"...\"",
            ],
        }
    metric = str((goal.front if goal else {}).get("primary_metric") or "")
    direction = str((goal.front if goal else {}).get("direction") or "minimize")

    for node in nodes:
        if node.status == "running":
            return {"action": "analyze", "experiment": node.id, "why": "a node is running"}
    for node in nodes:
        if node.status == "approved" and not node.runs:
            return {"action": "launch", "experiment": node.id, "why": "approved and never launched"}
    for node in nodes:
        if node.runs and not node.verdict and node.status != "concluded":
            return {"action": "analyze", "experiment": node.id, "why": "has runs, no verdict"}
    for node in nodes:
        if node.verdict in {"refutes", "inconclusive"} and not node.children:
            return {"action": "triage", "experiment": node.id, "why": "concluded, untriaged"}
    pending = [n.id for n in nodes if n.status == "proposed"]
    if pending:
        return {
            "action": "approve",
            "experiment": pending[0],
            "why": "proposed nodes await a decision",
            "pending": pending,
        }
    leader = best([r for n in nodes for r in n.runs], metric, direction) if metric else None
    return {
        "action": "exhausted",
        "why": f"all {len(nodes)} node(s) are concluded and nothing is proposed",
        "leader": leader,
    }
