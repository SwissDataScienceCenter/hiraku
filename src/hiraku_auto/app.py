"""Streamlit interface.

    hiraku-auto ui            # or: streamlit run -m hiraku_auto.app -- --project .

Read-first: the tree, each node's record, its runs, the pending proposals and the leaderboard. The
actions it offers are the same ones the CLI offers, and each one writes to the markdown records —
never to hidden UI state, so anything done here shows up in `git diff`.

Deliberately absent: editing prose in the browser. Findings and conclusions belong in the records,
in a diff a reviewer can read.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import streamlit as st

# Absolute imports: `streamlit run app.py` executes this file as a script,
# where relative imports have no parent package.
from hiraku_auto import agent as agent_mod
from hiraku_auto import gitops, scaffold
from hiraku_auto import promote as promote_mod
from hiraku_auto.config import load_project
from hiraku_auto.errors import HirakuError
from hiraku_auto.launcher import launch as do_launch
from hiraku_auto.launcher import load_entrypoints
from hiraku_auto.records import STATUSES, VERDICTS, load_experiment, save_experiment
from hiraku_auto.runs import rank
from hiraku_auto.tree import build

VERDICT_COLOR = {"supports": "#2f7a4f", "refutes": "#a13d2f", "inconclusive": "#96701f"}
STATUS_COLOR = {"running": "#2b6f8f", "proposed": "#7a6ba8", "approved": "#7a6ba8"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=".")
    known, _ = parser.parse_known_args(sys.argv[1:])
    return known


@st.cache_data(show_spinner=False)
def _entrypoints(root: str, experiment_id: str, experiments_dir: str) -> list[str]:
    try:
        return sorted(load_entrypoints(Path(experiments_dir) / experiment_id))
    except HirakuError:
        return []


def dot(node) -> str:
    color = VERDICT_COLOR.get(node.verdict) or STATUS_COLOR.get(node.status) or "#8a8a84"
    return f"<span style='color:{color}'>&#9679;</span>"


def main() -> None:
    st.set_page_config(page_title="hiraku-auto", layout="wide")
    args = parse_args()

    try:
        project = load_project(Path(args.project))
    except HirakuError as exc:
        st.error(str(exc))
        st.stop()

    forest = build(project)
    project_name = project.root.name
    if project.config_md.is_file():
        from hiraku_auto.records import read_record

        project_name = read_record(project.config_md).front.get("project") or project_name

    st.title(project_name)
    st.caption(
        f"{project.root} — the markdown records are the source of truth; this view is generated "
        f"from them."
    )

    counts = {
        "goals": len(forest.goals),
        "experiments": len(forest.nodes),
        "concluded": sum(1 for n in forest.nodes.values() if n.status == "concluded"),
        "open": sum(1 for n in forest.nodes.values() if n.status in {"running", "approved"}),
        "proposed": sum(1 for n in forest.nodes.values() if n.status == "proposed"),
        "runs": len(forest.table.rows),
    }
    for column, (label, value) in zip(st.columns(len(counts)), counts.items(), strict=True):
        column.metric(label, value)

    if forest.issues:
        with st.expander(f"{len(forest.issues)} record check(s)", expanded=False):
            for issue in forest.issues:
                st.write(f"- {issue}")

    if not forest.goals:
        st.info("No goals yet. Create one with `hiraku-auto goal add <id> --metric <name>`.")
        st.stop()

    left, right = st.columns([1, 1.35], gap="large")

    # ----------------------------------------------------------------- tree
    with left:
        st.subheader("Tree")
        selected = st.session_state.get("selected")
        for goal_id, goal in forest.goals.items():
            front = goal.front
            arrow = "↓" if front.get("direction") == "minimize" else "↑"
            target = front.get("target")
            st.markdown(
                f"**{goal_id}** &nbsp; `{front.get('primary_metric', '?')}` {arrow}"
                + (f" · target {target}" if target is not None else ""),
                unsafe_allow_html=True,
            )

            def walk(node_id: str, depth: int) -> None:
                node = forest.nodes[node_id]
                bits = [b for b in (node.kind, node.status, node.verdict) if b]
                origin = str(node.front.get("origin") or "")
                if origin.startswith("agent"):
                    bits.append(origin)
                if node.runs:
                    bits.append(f"{len(node.runs)} run{'s' if len(node.runs) != 1 else ''}")
                # Box-drawing glyphs rather than spaces: Streamlit collapses leading
                # whitespace, so indentation has to be printable characters.
                indent = ("&nbsp;" * 3).join([""] * (depth + 1)) + ("└─ " if depth else "")
                cols = st.columns([0.82, 0.18])
                cols[0].markdown(
                    f"<div style='white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>"
                    f"{indent}{dot(node)} <code>{node_id}</code> "
                    f"<span style='opacity:.6;font-size:.85em'>{' · '.join(bits)}</span></div>",
                    unsafe_allow_html=True,
                )
                if cols[1].button("open", key=f"open-{node_id}", use_container_width=True):
                    st.session_state["selected"] = node_id
                    st.rerun()
                for child in node.children:
                    walk(child, depth + 1)

            roots = forest.roots(goal_id)
            if not roots:
                st.caption("no experiments yet")
            for root in roots:
                walk(root, 0)
            st.divider()

        orphans = forest.unattached()
        if orphans:
            st.warning("Unattached (no reachable goal): " + ", ".join(orphans))

        with st.expander("Proposals awaiting a decision"):
            pending = {
                pid: r
                for pid, r in forest.proposals.items()
                if str(r.front.get("status") or "proposed") == "proposed"
            }
            if not pending:
                st.caption("none")
            for pid, record in pending.items():
                st.markdown(f"**{pid}** — {record.front.get('origin')}")
                st.caption(record.section("Hypothesis").strip()[:400])
                cols = st.columns(2)
                if cols[0].button("Accept", key=f"acc-{pid}"):
                    try:
                        scaffold.accept_proposal(project, pid)
                        st.success(f"created experiment {pid}")
                        st.rerun()
                    except HirakuError as exc:
                        st.error(str(exc))
                if cols[1].button("Decline", key=f"dec-{pid}"):
                    record.front["status"] = "declined"
                    record.front["decided_on"] = scaffold.today()
                    from hiraku_auto.records import save_proposal

                    save_proposal(record)
                    st.rerun()

    # ----------------------------------------------------------------- detail
    with right:
        selected = st.session_state.get("selected")
        if selected not in forest.nodes:
            selected = next(iter(forest.nodes), None)
        if selected is None:
            st.info("No experiments yet.")
            st.stop()

        node = forest.nodes[selected]
        st.subheader(selected)
        st.caption(
            " · ".join(
                str(x)
                for x in (node.kind, node.status, node.verdict, node.front.get("origin"))
                if x
            )
        )

        expectation = node.front.get("expectation")
        if expectation:
            st.markdown(f"**Expectation.** {expectation}")
        else:
            st.warning("No expectation recorded — this node cannot be triaged.")

        st.markdown(
            f"goal `{node.goal}` · parent `{node.parent or '— (root)'}` · "
            f"branch `{node.front.get('branch')}` · mode `{node.front.get('mode')}` · "
            f"depth {forest.depth(selected)}"
        )

        tabs = st.tabs(["Runs", "Record", "Act"])

        with tabs[0]:
            if not node.runs:
                st.caption("No runs recorded.")
            else:
                goal_front = forest.goals[node.goal].front if node.goal in forest.goals else {}
                metric = str(goal_front.get("primary_metric") or "")
                rows = node.runs
                if metric and any(r.get(metric) for r in rows):
                    rows = rank(rows, metric, descending=goal_front.get("direction") == "maximize")
                columns = [c for c in rows[0] if any(r.get(c) not in (None, "") for r in rows)]
                st.dataframe([{c: r.get(c, "") for c in columns} for r in rows], hide_index=True)
                if all(str(r.get("reproducible", "")).lower() != "true" for r in node.runs):
                    st.info("Every run here is a working-tree run, so none can be promoted.")

        with tabs[1]:
            st.code(node.record.path.read_text(encoding="utf-8"), language="markdown")

        with tabs[2]:
            st.markdown("##### Status")
            cols = st.columns(3)
            new_status = cols[0].selectbox(
                "status",
                STATUSES,
                index=STATUSES.index(node.status) if node.status in STATUSES else 0,
            )
            verdict_options = ["", *VERDICTS]
            new_verdict = cols[1].selectbox(
                "verdict",
                verdict_options,
                index=verdict_options.index(node.verdict) if node.verdict in verdict_options else 0,
            )
            if cols[2].button("Save", use_container_width=True):
                record = load_experiment(project, selected)
                record.front["status"] = new_status
                if new_verdict:
                    record.front["verdict"] = new_verdict
                    if new_status == "concluded" and not record.front.get("concluded"):
                        record.front["concluded"] = scaffold.today()
                else:
                    record.front.pop("verdict", None)
                save_experiment(record)
                scaffold.sync_children(project)
                st.success("saved")
                st.rerun()

            st.markdown("##### Launch")
            names = _entrypoints(str(project.root), selected, str(project.experiments_dir))
            if not names:
                st.caption("No launch.yml entrypoints for this experiment.")
            else:
                cols = st.columns([2, 1, 1])
                entrypoint = cols[0].selectbox("entrypoint", names)
                seed = cols[1].text_input("seed", value="1")
                reproducible = cols[2].checkbox("--commit HEAD", value=True)
                if st.button("Launch", type="primary"):
                    with st.spinner("running…"):
                        try:
                            result = do_launch(
                                project,
                                selected,
                                entrypoint,
                                commit="HEAD" if reproducible else None,
                                seed=seed or None,
                            )
                            st.success(f"run {result.run_id} — exit {result.exit_code}")
                            st.rerun()
                        except HirakuError as exc:
                            st.error(str(exc))

            st.markdown("##### Agent")
            st.caption(
                "The agent returns JSON; this app writes the records. Nothing is edited in the "
                "browser only."
            )
            cols = st.columns([1, 1])
            workflow = cols[0].selectbox("workflow", ["analyze", "triage", "design", "discover"])
            agent_cmd = cols[1].text_input("command", value=agent_mod.DEFAULT_AGENT_CMD)
            instruction = st.text_area("extra context (optional)", height=80)
            dry = st.checkbox("dry run — show the prompt only", value=False)
            if st.button("Run agent"):
                target = selected if workflow in {"analyze", "triage"} else node.goal
                runner = {
                    "analyze": agent_mod.run_analyze,
                    "triage": agent_mod.run_triage,
                    "design": agent_mod.run_design,
                    "discover": agent_mod.run_discover,
                }[workflow]
                with st.spinner(f"{workflow}…"):
                    try:
                        result = runner(
                            project,
                            target,
                            instruction=instruction,
                            command=agent_mod.agent_command(agent_cmd),
                            dry_run=dry,
                        )
                    except HirakuError as exc:
                        st.error(str(exc))
                        result = None
                if result is not None:
                    if dry:
                        st.code(result.prompt, language="markdown")
                    else:
                        if result.data.get("injected_instructions_found"):
                            st.warning(
                                "A retrieved source contained text addressed to the agent: "
                                + str(result.data["injected_instructions_found"])
                            )
                        for path in result.written:
                            st.write(f"wrote `{project.rel(path)}`")
                        with st.expander("agent JSON"):
                            st.json(result.data)
                        if result.written:
                            st.rerun()

            st.markdown("##### Promote")
            promotable = [r.get("run_id", "") for r in node.runs]
            if not promotable:
                st.caption("No runs to promote.")
            else:
                cols = st.columns([2, 2, 1])
                run_id = cols[0].selectbox("run", promotable)
                signer = cols[1].text_input("signed off by", value="")
                if cols[2].button("Gate", use_container_width=True):
                    try:
                        result = promote_mod.evaluate(
                            project, selected, run_id, signed_off_by=signer or None
                        )
                        for condition in result.conditions:
                            (st.success if condition.passed else st.error)(
                                f"{condition.name} — {condition.detail}"
                            )
                        if result.passed and st.session_state.get("confirm_promote") != run_id:
                            st.session_state["confirm_promote"] = run_id
                            st.info("Gate passed. Press Gate again to write the leaderboard entry.")
                        elif result.passed:
                            promote_mod.promote(
                                project, selected, run_id, signed_off_by=signer or None
                            )
                            st.success("promoted")
                            st.session_state.pop("confirm_promote", None)
                    except HirakuError as exc:
                        st.error(str(exc))

    with st.sidebar:
        st.markdown("### Next action")
        goal_choice = st.selectbox("goal", list(forest.goals))
        suggestion = agent_mod.suggest_next(forest, goal_choice)
        st.write(f"**{suggestion['action']}**")
        if suggestion.get("experiment"):
            st.code(suggestion["experiment"])
        st.caption(suggestion["why"])

        st.markdown("### Promoted")
        # Only the entries, not the file's explanatory preamble.
        entries = []
        if project.leaderboard.is_file():
            text = project.leaderboard.read_text(encoding="utf-8")
            entries = [b for b in text.split("### ")[1:] if "| Run |" in b]
        if entries:
            for entry in entries:
                st.caption(entry.splitlines()[0])
            with st.expander("full leaderboard"):
                st.markdown(project.leaderboard.read_text(encoding="utf-8"))
        else:
            st.caption("nothing promoted yet")

        st.markdown("### Worktrees")
        if gitops.is_repo(project.root):
            for entry in gitops.worktree_list(project.root):
                st.caption(f"{Path(entry.get('worktree', '')).name} → {entry.get('branch', '')}")
        else:
            st.caption("not a git repository")


main()
