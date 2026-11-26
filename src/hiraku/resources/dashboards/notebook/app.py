"""A minimal Marimo notebook for data exploration."""

import marimo

app = marimo.App()

with app.setup:
    import os

    import marimo as mo

    from hiraku.errors import HirakuError
    from hiraku.project import Project
    from hiraku.tracking import Leaderboard


@app.cell
def project_data():
    _root = os.environ.get("HIRAKU_PROJECT_ROOT")
    project = Project.discover(_root or mo.notebook_dir())
    records = project.vault.list()
    return project, records


@app.cell
def introduction(records):
    mo.md(f"# Exploration\n\n**Project records:** {len(records)}")
    return


@app.cell
def choose_leaderboard(project):
    leaderboard_names = Leaderboard.list(project)
    _requested = os.environ.get("HIRAKU_DASHBOARD_LEADERBOARD")
    _default = leaderboard_names[0] if len(leaderboard_names) == 1 else None
    leaderboard_selector = mo.ui.dropdown(
        leaderboard_names,
        value=_requested if _requested in leaderboard_names else _default,
        label="Leaderboard",
    )
    leaderboard_selector if leaderboard_names else mo.md("No leaderboards configured.")
    return leaderboard_names, leaderboard_selector


@app.cell
def leaderboard_data(project, leaderboard_selector):
    leaderboard = None
    runs = []
    _message = ""
    if leaderboard_selector.value is not None:
        try:
            leaderboard = Leaderboard.load(project, leaderboard_selector.value)
            runs = leaderboard.list_runs(project, include_unattributed=True)
            _message = f"**Runs:** {len(runs)}"
        except HirakuError as exc:
            _message = f"Leaderboard unavailable: {exc}"
    mo.md(_message)
    return leaderboard, runs


if __name__ == "__main__":
    app.run()
