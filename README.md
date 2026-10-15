# Hiraku

**Hiraku** is a toolkit for AI-assisted data science. It provides customizable workflows and a shared metadata record around the model development lifecycle of data science projects.

> **Why Hiraku?** It's a hint towards a possible integration with [renkulab.io](https://renkulab.io). In renku (連句), the traditional Japanese art of collaborative linked poetry, hiraku (平句) are the regular verses where the sequence develops, shifts, and explores new directions — a metaphor for iterative, exploratory research within the Renku ecosystem. *[Discuss here.](https://github.com/SwissDataScienceCenter/hiraku/issues/6)*

## Getting started

```bash
git clone https://github.com/SwissDataScienceCenter/hiraku.git
cd hiraku

# system install
uv tool install ".[mlflow,wandb,dashboard]"

# for local development
uv sync --all-extras
hiraku --help

# create a hiraku project
mkdir my-project
cd my-project
git init
hiraku init
```

Check the examples for a more complete walkthrough:
- [Heart-disease prediction using CSV logging](docs/examples/heart-disease/README.md)
- [Iris dataset using Weights & Biases](docs/examples/iris-wandb/README.md)
- [Digits dataset using MLFlow](docs/examples/digits-mlflow/README.md)
- [Karpathy's LLM autoresearch example](docs/examples/autoresearch/README.md)

## Dashboard

`hiraku dashboard` serves a local [marimo](https://marimo.io) page with the record tree, the runs attributed to each record, run details with links to the tracking backend, and a performance plot. It needs the `dashboard` extra.

| Command | What it does |
| --- | --- |
| `hiraku dashboard` | Serve the read-only dashboard at <http://127.0.0.1:8765> |
| `hiraku dashboard --include-code` | Also let the page show its notebook code (marimo menu, **⋯**) |
| `hiraku dashboard --init` | Copy the notebook to `hiraku/dashboard/app.py` so you can customize it |
| `hiraku dashboard --edit` | Open that copy in the marimo editor |
| `hiraku dashboard --init --edit` | Both in one step |

After `--init`, `hiraku dashboard` serves your copy. In the editor, the `runs_df` and `records_df` tables hold every run and record with its place in the tree, ready for your own plots or marimo's AI assistant. To try it with bundled MLflow results, initialize the Digits example with `hiraku init --example digits-mlflow --with-results`. See the [dashboard guide](docs/guides/dashboard.md) for details.

## Documentation

- [Get Started](https://swissdatasciencecenter.github.io/hiraku/)
- [Examples](https://swissdatasciencecenter.github.io/hiraku/examples/)
- [Guides](https://swissdatasciencecenter.github.io/hiraku/guides/)
- [Command-Line Interface](https://swissdatasciencecenter.github.io/hiraku/api/cli/)
- [Python Reference](https://swissdatasciencecenter.github.io/hiraku/api/)

## Contributing

Contributions are welcome. Pick a feature to add, create a feature branch and pull request. 

We track planned features and ideas using [GitHub issues](https://github.com/SwissDataScienceCenter/hiraku/issues). Read the [contribution guide](CONTRIBUTING.md) to get started.

**Unsure where to start but you know what you like to do?** Write a [*User Story*](https://github.com/SwissDataScienceCenter/hiraku/issues/new?labels=%22user%20story%22) to describe your idea and get feedback.
