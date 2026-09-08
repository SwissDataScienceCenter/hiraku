# Hiraku

**Hiraku** is a toolkit for AI-assisted data science. It provides customizable workflows and a shared metadata record around the model development lifecycle of data science projects.

> **Why Hiraku?** It's a hint towards a possible integration with [renkulab.io](https://renkulab.io). In renku (連句), the traditional Japanese art of collaborative linked poetry, hiraku (平句) are the regular verses where the sequence develops, shifts, and explores new directions — a metaphor for iterative, exploratory research within the Renku ecosystem. **Have a better day? [Discuss here.](https://github.com/SwissDataScienceCenter/hiraku/issues/6)**

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
hiraku init
```

Check the examples for a more complete walkthrough:
- [Heart-disease prediction using CSV logging](docs/examples/heart-disease/README.md)
- [Iris dataset using Weights & Biases](docs/examples/iris-wandb/README.md)
- [Digits dataset using MLFlow](docs/examples/digits-mlflow/README.md)
- [Kaparthy's LLM autoresearch example](docs/examples/kaparthy/README.md)

## Documentation

- [Get Started](https://swissdatasciencecenter.github.io/hiraku/)
- [Examples](http://localhost:8000/hiraku/examples/)
- [Guides](https://swissdatasciencecenter.github.io/hiraku/)
- [Command-Line Interface](https://swissdatasciencecenter.github.io/hiraku/api/cli/)
- [Python Reference](https://swissdatasciencecenter.github.io/hiraku/api/)

## Contributing

Contributions are welcome. Pick a feature to add, create a feature branch and pull request. Share and discuss :)

We track planned features and ideas using [GitHub issues](https://github.com/SwissDataScienceCenter/hiraku/issues). Read [Contributing](https://swissdatasciencecenter.github.io/hiraku/contributing/) to get started.