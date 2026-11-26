"""A minimal Marimo notebook for data exploration."""

import marimo

app = marimo.App()

with app.setup:
    import os

    import marimo as mo

    from hiraku.project import Project


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


if __name__ == "__main__":
    app.run()
