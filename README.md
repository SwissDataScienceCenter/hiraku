# Hiraku

**Hiraku** is a toolkit for AI-assisted data science. It provides customizable workflows and a shared metadata record around the model development lifecycle of data science projects.

> **Why Hiraku?** It's a hint towards a possible integration with [renkulab.io](https://renkulab.io). In renku (連句), the traditional Japanese art of collaborative linked poetry, hiraku (平句) are the regular verses where the sequence develops, shifts, and explores new directions — a metaphor for iterative, exploratory research within the Renku ecosystem. *[Discuss here.](https://github.com/SwissDataScienceCenter/hiraku/issues/6)*

## Getting started

```bash
git clone https://github.com/SwissDataScienceCenter/hiraku.git
cd hiraku

# system install
uv tool install ".[mlflow,wandb]"

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

## Dashboard demos

From a Hiraku source checkout, install the development environment and start a
standalone Digits demo with its dashboard and MLflow UI:

```bash
uv sync --all-extras
uv run --all-extras python scripts/use_case_dashboard.py --example digits-mlflow
```

The helper prints the dashboard URL, access token, tracking location, and demo
directory. Press **Ctrl-C** to stop the servers. Replace `digits-mlflow` with
`minimal`, `heart-disease`, `iris-wandb`, or `autoresearch` to run another
documented example. See the [dashboard demo guide](scripts/README-use-case-dashboard.md)
for requirements, options, and reopen instructions.

## Documentation

- [Interactive dashboard demos](scripts/README-use-case-dashboard.md) and
  [GUI manual](docs/guides/dashboard-manual.md)
- [Get Started](https://swissdatasciencecenter.github.io/hiraku/)
- [Examples](https://swissdatasciencecenter.github.io/hiraku/examples/)
- [Guides](https://swissdatasciencecenter.github.io/hiraku/guides/)
- [Command-Line Interface](https://swissdatasciencecenter.github.io/hiraku/api/cli/)
- [Python Reference](https://swissdatasciencecenter.github.io/hiraku/api/)

## Contributing

Contributions are welcome. Pick a feature to add, create a feature branch and pull request. 

We track planned features and ideas using [GitHub issues](https://github.com/SwissDataScienceCenter/hiraku/issues). Read the [contribution guide](CONTRIBUTING.md) to get started.

**Unsure where to start but you know what you like to do?** Write a [*User Story*](https://github.com/SwissDataScienceCenter/hiraku/issues/new?labels=%22user%20story%22) to describe your idea and get feedback.
