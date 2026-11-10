"""A minimal Marimo notebook for data exploration."""

import marimo

app = marimo.App()

with app.setup:
    import marimo as mo


@app.cell
def introduction():
    mo.md("# Exploration\n\nDescribe your question and dataset here.")
    return


@app.cell
def analysis():
    # Load data and start exploring here.
    return


if __name__ == "__main__":
    app.run()
