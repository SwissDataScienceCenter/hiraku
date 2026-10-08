"""`triage`: decide whether a concluded node is worth going further on."""

from __future__ import annotations

from typing import Any

from hiraku.config import HirakuConfig

from .. import sections
from ..engine import AgentRun, Hooks
from ..lock import writes
from ..node import (
    Node,
    create_node,
    list_nodes,
    load_node,
    task_of,
)
from ..replies import TriageReply
from ._common import AgentCall, _create_nodes, _NewNode, _write_sections, task_state


@writes("triage")
def run_triage(
    node_id: str,
    *,
    task_id: str | None = None,
    instruction: str = "",
    command: list[str] | None = None,
    dry_run: bool = False,
    apply: bool = True,
    context_paths: list[str] | None = None,
    repo_paths: list[str] | None = None,
    context_bytes: int | None = None,
    unset_env: list[str] | None = None,
    hooks: Hooks | None = None,
    timeout: int | None = None,
    action: str | None = None,
    context_id: str | None = None,
    config: HirakuConfig | None = None,
) -> AgentRun:
    """Judge whether a concluded node is worth going further on, and if so, how.

    Runs on a `concluded` node regardless of which way its verdict went — a
    `supports` can still be worth a robustness check or a natural ablation, exactly
    as a `refutes`/`inconclusive` can still be worth a diagnostic; see
    `prompts/triage.md` for the two different lenses it applies depending on which
    kind of verdict it's given. On success (not `blocked`), marks the node
    `triaged`, which — together with `abandoned` and having children — is one of
    the ways a node reaches `completed` (see `state.node_state`).

    Expects the node already has a verdict-worthy situation (usually
    `awaiting_analysis` or `concluded` — see `docs/state.md`); it does not check
    the derived state itself, since a researcher may reasonably triage a node the
    scheduler hasn't gotten to yet.
    """
    selected = config or HirakuConfig.discover()
    node = load_node(node_id, task=task_id, config=selected)
    task = task_of(node, config=selected)
    nodes = list_nodes(task=task, config=selected)
    call = AgentCall.resolve(
        selected,
        entity=task.id,
        action=action,
        context_id=context_id,
        command=command,
        timeout=timeout,
        context_paths=context_paths,
        context_bytes=context_bytes,
        repo_paths=repo_paths,
        hooks=hooks,
        unset_env=unset_env,
    )
    result = call.run(
        "triage",
        node.id,
        task_state(task, nodes, config=selected, focus=node.id),
        instruction=instruction,
        dry_run=dry_run,
        reply=TriageReply,
    )
    if dry_run:
        return result
    data = result.data
    if data.get("blocked") or not apply:
        return result

    # Reload: an agent call is minutes long, and writing from a stale copy would clobber
    # anything changed in the meantime.
    live = load_node(node.id, task=task, config=selected)
    markdown = live.markdown
    if data.get("findings"):
        markdown = sections.set_section(markdown, "Findings", str(data["findings"]))
    if data.get("conclusions"):
        markdown = sections.set_section(
            markdown, "Conclusions", str(data["conclusions"])
        )

    verdict = data.get("verdict")
    failure_class = str(data.get("failure_class") or "").strip().upper()
    if failure_class in {"B", "C"} and verdict in {"refutes", "inconclusive"}:
        live.verdict = verdict

    if failure_class == "A":
        # The runs did not test the expectation, so any verdict drawn from them is void
        # and there is nothing to decide about going further: the node needs a fix, not
        # a triage decision. `schedule.suggest_next` says so until other runs exist.
        run_ids = sorted(str(run.id) for run in live.runs(config=selected))
        live.verdict = None
        live.triaged = False
        live.execution_defect = True
        live.defect_runs = run_ids
        markdown = sections.set_section(
            markdown,
            "Execution defect",
            "\n\n".join(
                part
                for part in (
                    str(data.get("class_evidence") or "").strip(),
                    "Runs that did not test the expectation: "
                    + (", ".join(f"`{run}`" for run in run_ids) or "none"),
                    "Fix the code on this node, bind the new commit and launch again; "
                    "these runs are left out of the next analysis.",
                )
                if part
            ),
        )
        live.markdown = markdown
        live.save()
        result.written.append(live.path)
        return result

    test = data.get("investigation_test") or {}
    if isinstance(test, dict) and test:
        lines = ["**Investigation test.**", ""]
        for key, value in test.items():
            if not isinstance(value, dict):
                continue
            mark = "pass" if value.get("pass") else "FAIL"
            lines.append(f"- `{key}` — {mark}. {value.get('note', '')}".rstrip())
        if not data.get("proceed"):
            blocking = data.get("blocking_condition")
            lines += ["", f"Stopped: condition `{blocking}` failed."]
        markdown = sections.append_section(
            markdown, "Decisions and next steps", "\n".join(lines)
        )

    # Children first, all-or-nothing: if they cannot be written, the node must not be
    # marked as decided either.
    if data.get("proceed"):

        def build(child: dict[str, Any]) -> Node:
            child_node = create_node(
                str(child["id"]),
                task=task,
                parent=live.id,
                kind=str(child.get("kind") or "diagnostic"),
                origin="agent-diagnostic",
                expectation=str(child.get("expectation") or ""),
                mode=live.mode,
                config=selected,
            )
            _write_sections(
                child_node,
                child,
                {"scope": "Scope", "expectation_body": "Expectation detail"},
            )
            child_node.save()
            return child_node

        children = [
            child
            for child in data.get("children") or []
            if isinstance(child, dict) and child.get("id")
        ]
        created = _create_nodes(
            task,
            [
                _NewNode(str(child["id"]), live.id, lambda child=child: build(child))
                for child in children
            ],
            config=selected,
        )
        result.written.extend(node.path for node in created)

    # A completed triage call always records its decision, win or lose — this is
    # what lets `state.node_state` tell "never triaged" from "triaged, decided not
    # to go further" once there are no children to show for it either way.
    live.triaged = True
    live.markdown = markdown
    live.save()
    result.written.insert(0, live.path)
    return result


# ------------------------------------------------------------------------------ analyze
