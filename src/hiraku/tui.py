from __future__ import annotations

from typing import Any, ClassVar

from .app import HirakuApp
from .errors import HirakuError


def run_tui(core: HirakuApp) -> None:
    try:
        from textual.app import App, ComposeResult
        from textual.widgets import Footer, Header, Static, Tree
    except ImportError as exc:
        raise HirakuError("The TUI requires 'uv sync --extra tui'.") from exc

    goals = core.goals.list()
    run_rows = core.runs.rows()
    runs_by_experiment: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for run in run_rows:
        runs_by_experiment.setdefault((run["goal_id"], run["experiment_id"]), []).append(run)

    class HirakuTui(App[None]):
        TITLE = "Hiraku Research Lineage"
        CSS = """
        Tree { width: 2fr; }
        #help { width: 1fr; padding: 1 2; border-left: solid $primary; }
        """
        BINDINGS: ClassVar = [("q", "quit", "Quit")]

        def compose(self) -> ComposeResult:
            yield Header()
            tree: Tree[str] = Tree("Hiraku")
            tree.id = "lineage"
            for goal in goals:
                goal_node = tree.root.add(f"{goal.title} [{goal.metric} {goal.direction}]", expand=True)

                def add_experiments(parent_node: Any, nodes: list[dict[str, Any]], goal_id: str = goal.id) -> None:
                    for experiment in nodes:
                        label = (
                            f"{experiment['id']} ({experiment['kind']}, {experiment['status']}, "
                            f"{experiment['origin']}"
                            + (f", {experiment['verdict']}" if experiment["verdict"] else "")
                            + ")"
                        )
                        exp_node = parent_node.add(label, expand=True)
                        exp_node.add_leaf(f"hypothesis: {experiment['hypothesis']}")
                        commit = experiment["evaluated_commit"] or experiment["parent_commit"]
                        exp_node.add_leaf(f"commit: {commit[:12]}")
                        for run in runs_by_experiment.get((goal_id, experiment["id"]), []):
                            metrics = [
                                f"{key}={value}"
                                for key, value in run.items()
                                if key
                                not in {
                                    "id",
                                    "goal_id",
                                    "experiment_id",
                                    "commit_sha",
                                    "entrypoint",
                                    "status",
                                    "exit_code",
                                    "started_at",
                                    "finished_at",
                                    "provenance",
                                }
                                and not key.startswith("param/")
                            ]
                            run_node = exp_node.add(f"{run['id']} {run['status']} " + ", ".join(metrics))
                            parameters = [
                                f"{key.removeprefix('param/')}={value}"
                                for key, value in run.items()
                                if key.startswith("param/")
                            ]
                            if parameters:
                                run_node.add_leaf("parameters: " + ", ".join(parameters))
                            provenance = run["provenance"]
                            run_node.add_leaf(
                                "provenance: "
                                f"python={provenance.get('python')}, "
                                f"platform={provenance.get('platform')}, "
                                f"lockfile={provenance.get('lockfile')}"
                            )
                        add_experiments(exp_node, experiment["children"])

                add_experiments(goal_node, core.lineage.forest(goal.id))
                proposals = core.proposals.list(goal.id)
                if proposals:
                    proposal_node = goal_node.add("Auto proposals", expand=True)
                    for proposal in proposals:
                        proposal_node.add_leaf(f"{proposal['id']} → {proposal['experiment_id']} ({proposal['status']})")
            tree.root.expand()
            yield tree
            yield Static(
                "Read-only projection of .hiraku/state.db\n\n↑/↓ navigate  Enter expand/collapse\nq quit",
                id="help",
            )
            yield Footer()

    while True:
        HirakuTui().run()
        break
