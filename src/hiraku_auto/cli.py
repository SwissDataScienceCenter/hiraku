"""Command-line interface.

    hiraku-auto init
    hiraku-auto goal add | draft | list | show | rm
    hiraku-auto exp create | list | show | set | rm | tree
    hiraku-auto launch <experiment>:<entrypoint> [--commit REF]
    hiraku-auto runs [<experiment>] [--sort METRIC --descending]
    hiraku-auto agent draft|design|triage|discover|analyze
    hiraku-auto next <goal>
    hiraku-auto loop <goal>
    hiraku-auto promote <experiment> --run <run-id>
    hiraku-auto proposal list | accept | decline
    hiraku-auto worktree add | remove | list
    hiraku-auto check | sync | context | dash | ui

argparse rather than a CLI framework, so the package installs with no required dependencies.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import agent as agent_mod
from . import context as context_mod
from . import dash, gitops, scaffold
from . import promote as promote_mod
from . import report as report_mod
from .config import load_project
from .errors import HirakuError
from .launcher import launch as do_launch
from .records import (
    KINDS,
    MODES,
    STATUSES,
    VERDICTS,
    load_experiment,
    load_goals,
    load_proposals,
    save_experiment,
    save_proposal,
    unset_markers,
)
from .runs import rank, read_runs
from .tree import build, render_ascii, to_dict


def _red(text: str) -> str:
    """Highlight, but only where a terminal will render it. Piped output stays plain."""
    return f"\033[31m{text}\033[0m" if sys.stdout.isatty() else text


def _print_unset(project: Any, record: Any, *, prefix: str = "") -> int:
    """Print every value nobody has supplied yet. These are what stop a goal being usable."""
    markers = unset_markers(record)
    if not markers:
        return 0
    print(_red(f"\n{prefix}{len(markers)} value(s) still unset — fill these in before running:"))
    for section, needed in markers:
        print(_red(f"  [{section}] {needed}"))
    print(f"\n  edit {project.rel(record.path)}, then: hiraku-auto check")
    return len(markers)


def _table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "(no rows)"
    widths = {c: len(c) for c in columns}
    for row in rows:
        for column in columns:
            widths[column] = max(widths[column], len(str(row.get(column, ""))))
    header = "  ".join(c.ljust(widths[c]) for c in columns)
    line = "  ".join("-" * widths[c] for c in columns)
    body = [
        "  ".join(str(row.get(c, "")).ljust(widths[c]) for c in columns) for row in rows
    ]
    return "\n".join([header, line, *body])


def _project(args: argparse.Namespace):
    return load_project(Path(args.project) if getattr(args, "project", None) else None)


# --------------------------------------------------------------------------- commands


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.path or ".").resolve()
    # Validate before creating anything, so a refused init leaves no empty directory behind.
    if not args.allow_in_tool_repo and scaffold.tool_repo_root(root) is not None:
        scaffold.init_project(root, allow_in_tool_repo=False)  # raises with the full message
    root.mkdir(parents=True, exist_ok=True)
    written = scaffold.init_project(
        root,
        name=args.name,
        force=args.force,
        git=args.git,
        allow_in_tool_repo=args.allow_in_tool_repo,
    )
    for path in written:
        print(f"created {path.relative_to(root)}")
    if not written:
        print("nothing to do — project already initialised")
        return 0
    print(f"\nproject ready at {root}")
    if args.git and gitops.is_repo(root):
        print("git repository initialised")
    elif not gitops.is_repo(root):
        print("note: not a git repository. `git init` here, or re-run with --git —")
        print("      commit-backed runs and the promotion gate both need git.")
    print(f"\nnext:\n  cd {root}")
    print("  hiraku-auto goal add <goal-id> --metric <name> --direction minimize")
    return 0


def cmd_goal_add(args: argparse.Namespace) -> int:
    project = _project(args)
    path = scaffold.create_goal(
        project,
        args.id,
        primary_metric=args.metric,
        direction=args.direction,
        target=args.target,
        gate_metric=args.gate_metric,
        question=args.question or "",
    )
    print(f"created {project.rel(path)}")
    return 0


def cmd_goal_draft(args: argparse.Namespace) -> int:
    """Ask the agent to write the goal record, then hand it back for review.

    The blank template asks the researcher to invent a metric definition and an evaluation protocol
    from nothing. This asks the agent to draft both from the project's own files, and to mark every
    value it could not ground rather than guessing it. What comes back is a draft to correct.
    """
    project = _project(args)
    question = args.question or ""
    if args.question_file:
        question = Path(args.question_file).read_text(encoding="utf-8")
    if not question.strip() and not sys.stdin.isatty():
        question = sys.stdin.read()

    result = agent_mod.run_draft(
        project,
        args.id,
        question,
        instruction=args.instruction or "",
        command=agent_mod.agent_command(args.agent_cmd) if args.agent_cmd else None,
        dry_run=args.dry_run,
        apply=not args.no_apply,
        revise=args.revise,
        with_experiments=args.with_experiments,
        use_context=_context_choice(args),
        extra_context=args.context or [],
        timeout=args.timeout,
    )
    if args.dry_run:
        print(result.prompt)
        return 0
    print(result.context.summary())
    for path in result.written:
        print(f"wrote {project.rel(path)}")
    if not result.written:
        print("nothing written (--no-apply)")
        return 0

    record = load_goals(project).get(args.id)
    grounding = result.data.get("grounding") or []
    if not grounding:
        print(
            "\nwarning: the agent grounded nothing in your project files. Check that "
            "`hiraku-auto context` lists the files that describe this project."
        )
    else:
        print(f"\ngrounded {len(grounding)} claim(s) in the project's own files.")
    if result.data.get("notes"):
        print(f"\nnotes: {result.data['notes']}")

    outstanding = _print_unset(project, record) if record else 0
    print("\nnext:")
    print(f"  hiraku-auto goal show {args.id}          # read the whole draft")
    if outstanding:
        print("  # then replace the unset values above — the promotion gate compares every run")
        print("  # against this protocol, so a hole here means no run can ever be promoted")
    print(f"  hiraku-auto agent design {args.id}       # once the protocol is settled")
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    """What would be sent to the agent, before anything is sent. Look before you call."""
    project = _project(args)
    bundle = context_mod.collect(project, extra=args.add or [], enabled=True)
    if args.json:
        print(json.dumps(bundle.to_state(), indent=2))
        return 0
    print("sources: " + (", ".join(bundle.sources) or "(none configured)"))
    rows = [
        {
            "file": f.rel,
            "chars": f"{len(f.text):,}",
            "of": f"{f.bytes_total:,}" if f.truncated else "",
            "where": "another repo" if f.outside else "",
        }
        for f in bundle.files
    ]
    print(_table(rows, ["file", "chars", "of", "where"]))
    for note in bundle.skipped:
        print(f"omitted: {note}")
    print(f"\n{bundle.summary()}")
    print(
        "\nThese excerpts are included in the prompt for the draft, design and discover "
        "workflows.\nEdit `context.sources` in research/config.md to change the list, or pass "
        "--no-context\nto a single agent call. Paths may point outside this project — the code "
        "repository a\nresearch project studies is usually a separate checkout."
    )
    return 0


def cmd_goal_list(args: argparse.Namespace) -> int:
    project = _project(args)
    forest = build(project)
    rows = []
    for gid, record in load_goals(project).items():
        nodes = forest.subtree_all(gid)
        rows.append(
            {
                "goal": gid,
                "status": record.front.get("status", ""),
                "metric": record.front.get("primary_metric", ""),
                "direction": record.front.get("direction", ""),
                "target": record.front.get("target", ""),
                "nodes": len(nodes),
                "runs": sum(len(forest.nodes[n].runs) for n in nodes),
            }
        )
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print(_table(rows, ["goal", "status", "metric", "direction", "target", "nodes", "runs"]))
    return 0


def cmd_goal_show(args: argparse.Namespace) -> int:
    project = _project(args)
    record = load_goals(project).get(args.id)
    if record is None:
        raise HirakuError(f"Goal '{args.id}' does not exist.")
    if args.json:
        payload = {"front": record.front, "sections": record.sections}
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(record.path.read_text(encoding="utf-8"))
        _print_unset(project, record)
    return 0


def _report_removal(project: Any, removal: Any) -> int:
    for path in removal.paths:
        print(f"removed {project.rel(path)}")
    if removal.left_behind:
        print("\nleft alone (delete these yourself if you mean to):")
        for note in removal.left_behind:
            print(f"  {note}")
    print("\n  hiraku-auto check   # confirm nothing is left dangling")
    return 0


def cmd_goal_rm(args: argparse.Namespace) -> int:
    project = _project(args)
    removal = scaffold.remove_goal(
        project, args.id, with_experiments=args.with_experiments, force=args.force
    )
    return _report_removal(project, removal)


def cmd_exp_rm(args: argparse.Namespace) -> int:
    project = _project(args)
    removal = scaffold.remove_experiment(
        project, args.id, with_children=args.with_children, force=args.force
    )
    return _report_removal(project, removal)


def cmd_exp_create(args: argparse.Namespace) -> int:
    project = _project(args)
    path = scaffold.create_experiment(
        project,
        args.id,
        goal=args.goal,
        parent=args.parent,
        kind=args.kind,
        mode=args.mode,
        status=args.status,
        origin=args.origin,
        expectation=args.expectation or "",
        branch=args.branch,
    )
    print(f"created {project.rel(path)}")

    record = load_experiment(project, args.id)
    branch = str(record.front.get("branch"))
    # The record is already written, so a git problem is reported and does not abort: losing the
    # record because a branch could not be made would be the worse failure.
    if args.no_branch:
        print("branch not created (--no-branch)")
    elif not gitops.is_repo(project.root):
        print("branch not created (not a git repository)")
    elif not gitops.has_commits(project.root):
        print(
            f"branch not created: this repository has no commits yet. After the first commit, run"
            f"\n  git branch {branch}"
        )
    else:
        try:
            gitops.create_branch(project.root, branch)
            print(f"branch {branch}")
        except HirakuError as exc:
            print(f"branch not created: {exc}", file=sys.stderr)
    if args.worktree:
        target = project.root / ".worktrees" / args.id
        try:
            gitops.worktree_add(project.root, target, branch)
            print(f"worktree {project.rel(target)}")
        except HirakuError as exc:
            print(f"worktree not created: {exc}", file=sys.stderr)
    scaffold.sync_children(project)
    if not args.expectation:
        print("\nnote: no expectation recorded. Set one before launching:")
        print(f'  hiraku-auto exp set {args.id} --expectation "..."')
    return 0


def cmd_exp_list(args: argparse.Namespace) -> int:
    project = _project(args)
    forest = build(project)
    rows = []
    for nid in sorted(forest.nodes):
        node = forest.nodes[nid]
        if args.goal and node.goal != args.goal:
            continue
        rows.append(
            {
                "experiment": nid,
                "goal": node.goal or "",
                "parent": node.parent or "",
                "kind": node.kind,
                "status": node.status,
                "verdict": node.verdict,
                "origin": node.front.get("origin", ""),
                "runs": len(node.runs),
            }
        )
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print(
            _table(
                rows,
                ["experiment", "goal", "parent", "kind", "status", "verdict", "origin", "runs"],
            )
        )
    return 0


def cmd_exp_show(args: argparse.Namespace) -> int:
    project = _project(args)
    forest = build(project)
    if args.id not in forest.nodes:
        raise HirakuError(f"Experiment '{args.id}' does not exist.")
    node = forest.nodes[args.id]
    if args.json:
        print(json.dumps(to_dict(forest)["experiments"][args.id], indent=2, default=str))
        return 0
    print(node.record.path.read_text(encoding="utf-8"))
    if node.runs:
        print("\n## Runs\n")
        columns = [c for c in node.runs[0] if any(r.get(c) for r in node.runs)]
        print(_table(node.runs, columns))
    return 0


def cmd_exp_set(args: argparse.Namespace) -> int:
    project = _project(args)
    record = load_experiment(project, args.id)
    updates: dict[str, Any] = {}
    for key in ("status", "verdict", "kind", "mode", "goal", "parent", "origin", "expectation"):
        value = getattr(args, key, None)
        if value is not None:
            updates[key] = value
    if args.clear:
        for key in args.clear:
            updates[key] = None
    if not updates:
        raise HirakuError("Nothing to set.")
    if updates.get("status") == "concluded" and not (
        updates.get("verdict") or record.front.get("verdict")
    ):
        print("warning: concluding without a verdict — `check` will flag this", file=sys.stderr)
    scaffold.set_fields(record, updates)
    if updates.get("status") == "concluded" and not record.front.get("concluded"):
        record.front["concluded"] = scaffold.today()
    save_experiment(record)
    changed = ", ".join(f"{k}={v}" for k, v in updates.items())
    print(f"updated {project.rel(record.path)}: {changed}")
    scaffold.sync_children(project)
    return 0


def cmd_tree(args: argparse.Namespace) -> int:
    project = _project(args)
    forest = build(project)
    if args.json:
        print(json.dumps(to_dict(forest), indent=2, default=str))
        return 0
    print(render_ascii(forest, args.goal))
    if forest.issues:
        print(f"{len(forest.issues)} check(s) — run `hiraku-auto check`")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    project = _project(args)
    forest = build(project)
    if not forest.issues:
        print("no problems found")
        return 0
    for issue in forest.issues:
        print(issue)
    print(f"\n{len(forest.issues)} problem(s)")
    return 1 if args.strict else 0


def cmd_sync(args: argparse.Namespace) -> int:
    project = _project(args)
    touched = scaffold.sync_children(project)
    for path in touched:
        print(f"updated {project.rel(path)}")
    if not touched:
        print("children sections already up to date")
    return 0


def cmd_launch(args: argparse.Namespace) -> int:
    project = _project(args)
    if ":" not in args.target:
        raise HirakuError("Target must be <experiment>:<entrypoint>.")
    experiment_id, _, entrypoint = args.target.partition(":")
    result = do_launch(
        project,
        experiment_id,
        entrypoint,
        commit=args.commit,
        seed=args.seed,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        return 0
    kind = "reproducible" if result.reproducible else "NOT reproducible (working tree)"
    print(f"\nrun {result.run_id} — exit {result.exit_code}, {kind}")
    metrics = {
        k: v
        for k, v in result.row.items()
        if k not in dict.fromkeys(
            [
                "run_id",
                "experiment_id",
                "commit",
                "run_kind",
                "reproducible",
                "launched_at",
                "entrypoint",
                "exit_code",
                "duration_s",
                "seed",
                "python_version",
                "lockfile_sha256",
                "platform",
                "accelerator",
            ]
        )
    }
    if metrics:
        print("metrics: " + ", ".join(f"{k}={v}" for k, v in metrics.items()))
    else:
        print(
            "no metrics recorded — write a JSON object to $HIRAKU_METRICS_PATH from your entrypoint"
        )
    if not result.reproducible:
        print("note: a working-tree run can never be promoted. Re-run with --commit HEAD.")
    return result.exit_code


def cmd_runs(args: argparse.Namespace) -> int:
    project = _project(args)
    table = read_runs(project.table_path)
    rows = table.for_experiment(args.experiment) if args.experiment else table.rows
    if args.sort:
        if args.ascending == args.descending:
            raise HirakuError("--sort requires exactly one of --ascending or --descending.")
        rows = rank(rows, args.sort, descending=args.descending)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("(no runs)")
        return 0
    columns = [c for c in rows[0] if any(r.get(c) not in (None, "") for r in rows)]
    if "_rank" in columns:
        columns = ["_rank"] + [c for c in columns if c != "_rank"]
    print(_table(rows, columns))
    return 0


def _context_choice(args: argparse.Namespace) -> bool | None:
    """None means "whatever this workflow does by default"."""
    if getattr(args, "no_context", False):
        return False
    if getattr(args, "context", None):
        return True
    return None


def _warn_unset_goal(project: Any, goal_id: str) -> None:
    """A goal with holes in its protocol produces work that cannot be promoted. Say so first."""
    record = load_goals(project).get(goal_id)
    if record is None:
        return
    markers = unset_markers(record)
    if not markers:
        return
    print(
        _red(
            f"warning: goal '{goal_id}' has {len(markers)} unset value(s) in its record. "
            "Gate condition 4\n"
            "         compares a run against this goal's evaluation protocol, so anything "
            "designed now\n"
            "         may be unpromotable. `hiraku-auto goal show " + goal_id + "` lists them.\n"
        )
    )


def cmd_agent(args: argparse.Namespace) -> int:
    project = _project(args)
    command = agent_mod.agent_command(args.agent_cmd) if args.agent_cmd else None
    kwargs = dict(
        instruction=args.instruction or "",
        command=command,
        dry_run=args.dry_run,
        apply=not args.no_apply,
        use_context=_context_choice(args),
        extra_context=args.context or [],
        timeout=args.timeout,
    )
    if args.workflow in {"design", "discover"} and not args.dry_run:
        _warn_unset_goal(project, args.target)
    if args.workflow == "draft":
        raise HirakuError(
            "Drafting a goal is `hiraku-auto goal draft <id> --question \"...\"` — it needs the "
            "question, which this command has nowhere to put."
        )
    if args.workflow == "design":
        result = agent_mod.run_design(project, args.target, **kwargs)
    elif args.workflow == "discover":
        result = agent_mod.run_discover(project, args.target, **kwargs)
    elif args.workflow == "triage":
        result = agent_mod.run_triage(project, args.target, **kwargs)
    elif args.workflow == "analyze":
        result = agent_mod.run_analyze(project, args.target, **kwargs)
    else:  # pragma: no cover - argparse restricts this
        raise HirakuError(f"Unknown workflow '{args.workflow}'.")

    if args.dry_run:
        print(result.prompt)
        return 0
    if result.context:
        print(result.context.summary())
    if args.show_raw:
        print(result.raw)
    if result.data.get("blocked"):
        print(f"blocked: {result.data['blocked']}")
        return 1
    if result.data.get("injected_instructions_found"):
        print("WARNING — a retrieved source contained text addressed to the agent:")
        print(f"  {result.data['injected_instructions_found']}")
    for path in result.written:
        print(f"wrote {project.rel(path)}")
    if not result.written:
        print("agent returned nothing to write")
    if result.data.get("notes"):
        print(f"\nnotes: {result.data['notes']}")
    if args.workflow == "triage":
        failure_class = str(result.data.get("failure_class") or "")
        if failure_class == "A":
            print(
                "\nclass A (execution defect): the expectation was never tested. Fix on this node "
                "and re-run — no verdict set, no child created."
            )
        elif not result.data.get("proceed"):
            blocking = str(result.data.get("blocking_condition") or "unnamed")
            print(f"\ninvestigation stopped — the investigation test failed on: {blocking}")
    scaffold.sync_children(project)
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    project = _project(args)
    forest = build(project)
    suggestion = agent_mod.suggest_next(forest, args.goal)
    if args.json:
        print(json.dumps(suggestion, indent=2, default=str))
        return 0
    print(f"action:     {suggestion['action']}")
    if suggestion.get("experiment"):
        print(f"experiment: {suggestion['experiment']}")
    print(f"why:        {suggestion['why']}")
    if suggestion.get("pending"):
        print("pending:    " + ", ".join(suggestion["pending"]))
    if suggestion.get("leader"):
        print(f"leader:     {suggestion['leader'].get('run_id')}")
    for line in suggestion.get("next_commands") or []:
        print(f"  {line}")
    return 0


def _launchable(forest, goal_id: str) -> list[str]:
    """Approved nodes with no runs yet — the ones a batch may start."""
    return [
        nid
        for nid in forest.subtree_all(goal_id)
        if forest.nodes[nid].status == "approved" and not forest.nodes[nid].runs
    ]


def _devices(spec: str | None, slots: int) -> list[str]:
    """One CUDA_VISIBLE_DEVICES value per concurrent slot.

    `--gpus 0,1,2,3` pins explicit devices; `--gpus 8` means devices 0..7. Empty means the runs
    inherit whatever the caller had, which is right for CPU work and wrong for a shared cluster.
    """
    if not spec:
        return [""] * slots
    if "," in spec:
        ids = [s.strip() for s in spec.split(",") if s.strip()]
    else:
        try:
            ids = [str(i) for i in range(int(spec))]
        except ValueError:
            ids = [spec.strip()]
    if not ids:
        return [""] * slots
    return [ids[i % len(ids)] for i in range(slots)]


def launch_batch(
    project,
    experiment_ids: list[str],
    entrypoint: str,
    *,
    max_parallel: int,
    gpus: str | None = None,
    commit: str | None = None,
    seed: Any = None,
) -> list[tuple[str, Any]]:
    """Launch several experiments at once, one GPU per slot.

    Each run defaults to its own branch as the commit ref, so parallel runs are isolated by the
    launcher's own temporary worktrees and stay reproducible — no durable worktree needed, and no
    two runs sharing a checkout.
    """
    import concurrent.futures as futures

    devices = _devices(gpus, max_parallel)
    results: list[tuple[str, Any]] = []

    def one(index: int, experiment_id: str):
        record = load_experiment(project, experiment_id)
        ref = commit
        if ref is None:
            branch = str(record.front.get("branch") or "")
            ref = branch if branch and gitops.branch_exists(project.root, branch) else "HEAD"
        device = devices[index % len(devices)]
        extra = {"CUDA_VISIBLE_DEVICES": device} if device else None
        return do_launch(
            project, experiment_id, entrypoint, commit=ref, seed=seed, extra_env=extra
        )

    with futures.ThreadPoolExecutor(max_workers=max(1, max_parallel)) as pool:
        pending = {
            pool.submit(one, index, eid): eid for index, eid in enumerate(experiment_ids)
        }
        for future in futures.as_completed(pending):
            experiment_id = pending[future]
            try:
                results.append((experiment_id, future.result()))
            except HirakuError as exc:
                print(f"  {experiment_id}: {exc}", file=sys.stderr)
                results.append((experiment_id, None))
    return results


def cmd_report(args: argparse.Namespace) -> int:
    project = _project(args)
    text = report_mod.render(project, args.goal)
    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        print(f"wrote {output}")
    else:
        print(text)
    return 0


def cmd_loop(args: argparse.Namespace) -> int:
    """Bounded loop. Stops at every point the policy says a human decides."""
    project = _project(args)
    for iteration in range(1, args.max_iterations + 1):
        forest = build(project)
        suggestion = agent_mod.suggest_next(forest, args.goal)
        action = suggestion["action"]
        print(f"\n[{iteration}/{args.max_iterations}] {action}: {suggestion['why']}")

        if action == "exhausted":
            print("goal exhausted — every node concluded and nothing proposed")
            return 0
        if action == "approve":
            print("stopping: proposed nodes need a decision.")
            print("  " + ", ".join(suggestion.get("pending", [])))
            print("  approve with: hiraku-auto exp set <id> --status approved")
            return 0
        if action == "launch":
            if not args.launch:
                print(f"stopping: '{suggestion['experiment']}' is ready to launch.")
                print("  pass --launch <entrypoint> to let the loop launch it")
                return 0
            batch = _launchable(forest, args.goal)[: max(1, args.max_parallel)]
            if not batch:
                batch = [suggestion["experiment"]]
            if len(batch) > 1:
                print(
                    f"  launching {len(batch)} in parallel"
                    + (f" on devices {args.gpus}" if args.gpus else "")
                    + ": "
                    + ", ".join(batch)
                )
            results = launch_batch(
                project,
                batch,
                args.launch,
                max_parallel=args.max_parallel,
                gpus=args.gpus,
                commit=args.commit,
                seed=args.seed,
            )
            failed = []
            for experiment_id, result in results:
                if result is None:
                    failed.append(experiment_id)
                    continue
                print(f"  {experiment_id}: run {result.run_id}, exit {result.exit_code}")
                if result.exit_code != 0:
                    failed.append(experiment_id)
            if failed:
                print(f"stopping: {len(failed)} run(s) failed: {', '.join(failed)}")
                return 1
            continue
        if action in {"analyze", "triage"}:
            runner = agent_mod.run_analyze if action == "analyze" else agent_mod.run_triage
            result = runner(
                project,
                suggestion["experiment"],
                command=agent_mod.agent_command(args.agent_cmd) if args.agent_cmd else None,
                timeout=args.timeout,
            )
            for path in result.written:
                print(f"  wrote {project.rel(path)}")
            if action == "triage" and not result.data.get("proceed"):
                print(
                    f"stopping: investigation test failed on "
                    f"`{result.data.get('blocking_condition')}`"
                )
                return 0
            continue
    print(f"\nstopping: reached --max-iterations {args.max_iterations}")
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    project = _project(args)
    result = promote_mod.promote(
        project,
        args.experiment,
        args.run,
        signed_off_by=args.signed_off_by,
        force=args.force,
    )
    for condition in result.conditions:
        mark = "pass" if condition.passed else "FAIL"
        print(f"  {condition.name:14s} {mark}  {condition.detail}")
    if result.passed:
        print(f"\npromoted to {project.rel(project.leaderboard)}")
        return 0
    if args.force:
        print(f"\nFORCED into {project.rel(project.leaderboard)} despite failures")
        return 0
    print(f"\nnot promoted — {len(result.failures())} condition(s) failed")
    return 1


def cmd_proposal_list(args: argparse.Namespace) -> int:
    project = _project(args)
    rows = [
        {
            "proposal": pid,
            "goal": r.front.get("goal", ""),
            "origin": r.front.get("origin", ""),
            "kind": r.front.get("kind", ""),
            "status": r.front.get("status", ""),
            "proposed": r.front.get("proposed_on", ""),
        }
        for pid, r in load_proposals(project).items()
    ]
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
    else:
        print(_table(rows, ["proposal", "goal", "origin", "kind", "status", "proposed"]))
    return 0


def cmd_proposal_accept(args: argparse.Namespace) -> int:
    project = _project(args)
    path = scaffold.accept_proposal(project, args.id, mode=args.mode)
    print(f"created {project.rel(path)}")
    if gitops.is_repo(project.root):
        record = load_experiment(project, args.id)
        gitops.create_branch(project.root, str(record.front.get("branch")))
        print(f"branch {record.front.get('branch')}")
    scaffold.sync_children(project)
    return 0


def cmd_proposal_decline(args: argparse.Namespace) -> int:
    project = _project(args)
    proposals = load_proposals(project)
    if args.id not in proposals:
        raise HirakuError(f"Proposal '{args.id}' does not exist.")
    record = proposals[args.id]
    record.front["status"] = "declined"
    record.front["decided_on"] = scaffold.today()
    record.append_section(
        "Decision", f"Declined on {scaffold.today()}. {args.reason or '**[No reason given.]**'}"
    )
    save_proposal(record)
    print(f"declined {project.rel(record.path)} (kept — it is what stops this being re-proposed)")
    return 0


def cmd_worktree(args: argparse.Namespace) -> int:
    project = _project(args)
    if args.action == "list":
        for entry in gitops.worktree_list(project.root):
            print(f"{entry.get('worktree', '')}  {entry.get('branch', entry.get('HEAD', ''))}")
        return 0
    record = load_experiment(project, args.id)
    branch = str(record.front.get("branch"))
    target = project.root / ".worktrees" / args.id
    if args.action == "add":
        gitops.worktree_add(project.root, target, branch)
        print(f"worktree {project.rel(target)} on {branch}")
    else:
        gitops.worktree_remove(project.root, target, force=args.force)
        print(f"removed {project.rel(target)}")
    return 0


def cmd_dash(args: argparse.Namespace) -> int:
    project = _project(args)
    forest = build(project)
    html_text = dash.render(dash.adapt(to_dict(forest), project))
    output = Path(args.out)
    output.write_text(html_text, encoding="utf-8")
    print(
        f"{output}: {len(forest.goals)} goals, {len(forest.nodes)} experiments, "
        f"{len(forest.table.rows)} runs"
    )
    for issue in forest.issues:
        print(f"  check: {issue}", file=sys.stderr)
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    import shutil
    import subprocess

    if shutil.which("streamlit") is None:
        raise HirakuError(
            "streamlit is not installed. Install it with:  pip install 'hiraku-auto[ui]'"
        )
    app = Path(__file__).parent / "app.py"
    project = _project(args)
    return subprocess.call(
        [
            "streamlit",
            "run",
            str(app),
            "--server.port",
            str(args.port),
            "--",
            "--project",
            str(project.root),
        ]
    )


# --------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hiraku-auto",
        description="Autoresearch over a Hiraku experiment tree: goals, parallel hypotheses, "
        "failure triage, a gated leaderboard, and an agent that designs and analyses.",
    )
    parser.add_argument(
        "--project", help="project directory (default: search upward for .hiraku.yml)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create a research project (in its own directory)")
    p.add_argument("path", nargs="?", default=".", help="created if it does not exist")
    p.add_argument("--name", help="project name written into research/config.md")
    p.add_argument("--git", action="store_true", help="also run `git init` if needed")
    p.add_argument("--force", action="store_true", help="fill in missing files; never overwrites")
    p.add_argument(
        "--allow-in-tool-repo",
        action="store_true",
        help="permit init inside the hiraku-auto source tree (you almost never want this)",
    )
    p.set_defaults(func=cmd_init)

    goal = sub.add_parser(
        "goal", help="research questions: the metric, the target, the evaluation protocol"
    ).add_subparsers(dest="a", required=True)
    p = goal.add_parser("add", help="create a goal")
    p.add_argument("id")
    p.add_argument("--metric", required=True, help="primary metric name")
    p.add_argument("--direction", choices=["minimize", "maximize"], default="minimize")
    p.add_argument("--target", help="the value that would settle the question")
    p.add_argument("--gate-metric", help="a metric that must not degrade (e.g. completion_rate)")
    p.add_argument("--question")
    p.set_defaults(func=cmd_goal_add)
    p = goal.add_parser(
        "draft",
        help="write the goal record with the agent, from a question and the project's own files",
    )
    p.add_argument("id")
    p.add_argument("--question", help="the research question, in your own words")
    p.add_argument("--question-file", metavar="PATH", help="read the question from a file")
    p.add_argument(
        "--revise",
        action="store_true",
        help="rewrite an existing goal's drafted sections, keeping its History",
    )
    p.add_argument(
        "--with-experiments",
        action="store_true",
        help="also create the agent's suggested first experiments, as proposed nodes",
    )
    p.add_argument("--instruction", help="extra context for the agent")
    p.add_argument(
        "--context",
        action="append",
        metavar="PATH",
        help="an extra file or glob to send along; repeatable",
    )
    p.add_argument(
        "--no-context", action="store_true", help="send no project files at all"
    )
    p.add_argument("--agent-cmd", help=f"default: {agent_mod.DEFAULT_AGENT_CMD!r}")
    p.add_argument("--dry-run", action="store_true", help="print the prompt, call nothing")
    p.add_argument("--no-apply", action="store_true", help="call the agent but write no files")
    p.add_argument("--timeout", type=int, default=agent_mod.DEFAULT_TIMEOUT, help="seconds")
    p.set_defaults(func=cmd_goal_draft)
    p = goal.add_parser("list", help="one row per goal, with node and run counts")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_goal_list)
    p = goal.add_parser("show", help="print a goal record")
    p.add_argument("id")
    p.add_argument("--json", action="store_true", help="front matter and sections as JSON")
    p.set_defaults(func=cmd_goal_show)
    p = goal.add_parser(
        "rm", help="delete a goal record; refuses while experiments or runs depend on it"
    )
    p.add_argument("id")
    p.add_argument(
        "--with-experiments",
        action="store_true",
        help="delete the goal's experiment records too (branches and worktrees are kept)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="delete even though runs were recorded; the run rows stay and become orphans",
    )
    p.set_defaults(func=cmd_goal_rm)

    exp = sub.add_parser(
        "exp", help="experiments; a sub-experiment is an experiment with a --parent"
    ).add_subparsers(dest="a", required=True)
    p = exp.add_parser("create", help="create an experiment and its branch")
    p.add_argument("id")
    p.add_argument("--goal")
    p.add_argument("--parent", help="the node whose result this responds to")
    p.add_argument("--kind", choices=list(KINDS), default="hypothesis")
    p.add_argument("--mode", choices=list(MODES), default="guided")
    p.add_argument("--status", choices=list(STATUSES), default="proposed")
    p.add_argument(
        "--origin", default="researcher", help="researcher | agent-diagnostic | agent-literature"
    )
    p.add_argument("--expectation", help="one falsifiable sentence; required before launching")
    p.add_argument("--branch", help="default: exp/<id>; must be a flat name")
    p.add_argument("--no-branch", action="store_true", help="write the record only")
    p.add_argument("--worktree", action="store_true", help="also add .worktrees/<id>")
    p.set_defaults(func=cmd_exp_create)
    p = exp.add_parser("list", help="one row per experiment")
    p.add_argument("--goal", help="restrict to one goal")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_exp_list)
    p = exp.add_parser("show", help="print a record and its runs")
    p.add_argument("id")
    p.add_argument("--json", action="store_true", help="record, lineage and runs as JSON")
    p.set_defaults(func=cmd_exp_show)
    p = exp.add_parser("set", help="edit front matter on an existing record")
    p.add_argument("id")
    p.add_argument("--status", choices=list(STATUSES))
    p.add_argument("--verdict", choices=list(VERDICTS), help="only meaningful when concluded")
    p.add_argument("--kind", choices=list(KINDS))
    p.add_argument("--mode", choices=list(MODES), help="may narrow the parent's, never widen it")
    p.add_argument("--goal")
    p.add_argument("--parent", help="reparent: one line, no files move")
    p.add_argument("--origin")
    p.add_argument("--expectation")
    p.add_argument("--clear", nargs="+", metavar="KEY", help="remove these keys")
    p.set_defaults(func=cmd_exp_set)
    p = exp.add_parser(
        "rm", help="delete an experiment record; refuses while children or runs depend on it"
    )
    p.add_argument("id")
    p.add_argument(
        "--with-children",
        action="store_true",
        help="delete the whole subtree beneath it as well",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="delete even though runs were recorded; the run rows stay and become orphans",
    )
    p.set_defaults(func=cmd_exp_rm)

    p = sub.add_parser("tree", help="render the experiment tree")
    p.add_argument("--goal", help="one goal only")
    p.add_argument(
        "--json", action="store_true", help="full snapshot: the same one the agent and viewers read"
    )
    p.set_defaults(func=cmd_tree)

    p = sub.add_parser("check", help="validate every record and the run table")
    p.add_argument("--strict", action="store_true", help="exit non-zero when problems are found")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("sync", help="regenerate Children sections from parent links")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser(
        "context", help="list the project files the agent would be given, before sending any"
    )
    p.add_argument(
        "--add", action="append", metavar="PATH", help="also resolve this file or glob"
    )
    p.add_argument("--json", action="store_true", help="the bundle exactly as the agent sees it")
    p.set_defaults(func=cmd_context)

    p = sub.add_parser("launch", help="run an entrypoint and record the run")
    p.add_argument("target", metavar="EXPERIMENT:ENTRYPOINT")
    p.add_argument(
        "--commit",
        metavar="REF",
        help="run this commit in a temporary worktree (reproducible)",
    )
    p.add_argument("--seed", help="recorded, and exported as HIRAKU_SEED")
    p.add_argument("--dry-run", action="store_true", help="print what would run; execute nothing")
    p.set_defaults(func=cmd_launch)

    p = sub.add_parser("runs", help="list or rank runs")
    p.add_argument("experiment", nargs="?", help="omit for every run in the project")
    p.add_argument("--sort", metavar="METRIC", help="rank by a numeric column")
    p.add_argument("--ascending", action="store_true", help="lower is better")
    p.add_argument("--descending", action="store_true", help="higher is better")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_runs)

    p = sub.add_parser("agent", help="call Claude Code to design, triage, discover or analyse")
    p.add_argument("workflow", choices=list(agent_mod.WORKFLOWS))
    p.add_argument("target", help="goal id for design/discover; experiment id for triage/analyze")
    p.add_argument("--instruction", help="extra context for the agent")
    p.add_argument(
        "--context",
        action="append",
        metavar="PATH",
        help="an extra project file or glob to send along; repeatable",
    )
    p.add_argument(
        "--no-context",
        action="store_true",
        help="send no project files (design and discover send them by default)",
    )
    p.add_argument("--agent-cmd", help=f"default: {agent_mod.DEFAULT_AGENT_CMD!r}")
    p.add_argument("--dry-run", action="store_true", help="print the prompt, call nothing")
    p.add_argument("--no-apply", action="store_true", help="call the agent but write no files")
    p.add_argument("--show-raw", action="store_true", help="also print the raw reply")
    p.add_argument("--timeout", type=int, default=agent_mod.DEFAULT_TIMEOUT, help="seconds")
    p.set_defaults(func=cmd_agent)

    p = sub.add_parser("next", help="what the loop would do next (no agent call)")
    p.add_argument("goal")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_next)

    p = sub.add_parser("loop", help="bounded loop; stops wherever a human decides")
    p.add_argument("goal")
    p.add_argument("--max-iterations", type=int, default=5, help="hard cap on agent calls")
    p.add_argument(
        "--max-parallel",
        type=int,
        default=1,
        metavar="N",
        help="launch up to N approved experiments at once (default 1, i.e. sequential)",
    )
    p.add_argument(
        "--gpus",
        metavar="SPEC",
        help="'8' for devices 0-7, or an explicit list '0,1,4,5'; one device per slot via "
        "CUDA_VISIBLE_DEVICES",
    )
    p.add_argument(
        "--launch", metavar="ENTRYPOINT", help="allow the loop to launch this entrypoint"
    )
    p.add_argument(
        "--commit",
        help="ref for launches the loop makes; default is each experiment's own branch",
    )
    p.add_argument("--seed")
    p.add_argument("--agent-cmd", help=f"default: {agent_mod.DEFAULT_AGENT_CMD!r}")
    p.add_argument("--timeout", type=int, default=agent_mod.DEFAULT_TIMEOUT, help="seconds")
    p.set_defaults(func=cmd_loop)

    p = sub.add_parser("promote", help="put a run through the promotion gate")
    p.add_argument("experiment")
    p.add_argument("--run", required=True, metavar="RUN_ID")
    p.add_argument("--signed-off-by", metavar="NAME", help="gate condition 5, in guided mode")
    p.add_argument("--force", action="store_true", help="record it anyway, marked as forced")
    p.set_defaults(func=cmd_promote)

    prop = sub.add_parser(
        "proposal", help="agent proposals awaiting a decision"
    ).add_subparsers(dest="a", required=True)
    p = prop.add_parser("list", help="the pending queue")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_proposal_list)
    p = prop.add_parser("accept", help="turn a proposal into an experiment")
    p.add_argument("id")
    p.add_argument("--mode", choices=list(MODES), default="guided")
    p.set_defaults(func=cmd_proposal_accept)
    p = prop.add_parser("decline", help="record a decision; the file is kept")
    p.add_argument("id")
    p.add_argument("--reason", help="why — it counts against novelty later")
    p.set_defaults(func=cmd_proposal_decline)

    wt = sub.add_parser("worktree", help="one checkout per active experiment")
    wt.add_argument("action", choices=["add", "remove", "list"])
    wt.add_argument("id", nargs="?", help="experiment id; omit for `list`")
    wt.add_argument("--force", action="store_true", help="remove even with local changes")
    wt.set_defaults(func=cmd_worktree)

    p = sub.add_parser(
        "report", help="assemble a goal's report from the records: known, ruled out, still open"
    )
    p.add_argument("goal")
    p.add_argument("-o", "--out", metavar="FILE", help="write to a file instead of stdout")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("dash", help="write the HTML tree viewer")
    p.add_argument("-o", "--out", default="dashboard.html", help="output file")
    p.set_defaults(func=cmd_dash)

    p = sub.add_parser("ui", help="run the Streamlit interface")
    p.add_argument("--port", type=int, default=8501, help="Streamlit port")
    p.set_defaults(func=cmd_ui)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except HirakuError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
