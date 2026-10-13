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

Replace `digits-mlflow` with `minimal`, `heart-disease`, `iris-wandb`, or
`autoresearch` to choose another example.

### What happens when you run a demo

1. The helper creates an isolated project with example scripts, a local Git
   repository, and research records organized into tasks and experiments.
2. It runs the initial experiments listed below and logs their metrics and
   provenance. Training finishes before the dashboard opens.
3. It starts an interactive dashboard at `http://127.0.0.1:8765` and normally
   opens your browser. Digits also starts the MLflow UI on port 5000.
4. The terminal prints the full dashboard URL, access token, tracking location,
   and **Demo project** path. Use the full URL, including `access_token`, to
   access the GUI. Keep the token private: it allows record creation and launches.

| Demo | Initial results |
| --- | --- |
| `minimal` | One baseline evaluation, logged to CSV |
| `heart-disease` | Logistic and random-forest training runs, logged to CSV |
| `digits-mlflow` | Two short training runs, with metrics and artifacts in MLflow |
| `iris-wandb` | Prepared records; authenticate with W&B and use `--run-iris` to log runs |
| `autoresearch` | Three simulated CPU runs, logged to CSV; no GPU required |

For Autoresearch-style plots on macOS or Linux, run:

```bash
uv run --all-extras python scripts/use_case_dashboard.py --example autoresearch
```

This CPU demo opens with three simulated results for validation loss and memory
usage. It requires no GPU; actual Autoresearch training is described in its
example guide.

### Using the GUI

The dashboard opens with the record tree visible and the other sections
collapsed. Click a section title to expand or collapse it; opened sections
stay open during refreshes.

- **Records:** Click a tree node or use the record selector to choose a task
  or experiment. Search the tree, expand branches, or zoom to explore it.
- **Record detail:** Inspect the selected record's description, effective
  fields, and where inherited values were defined.
- **Runs:** Compare tracked results and inspect a selected run's record,
  entrypoint, commit, metrics, and tracking-backend link when available.
  The demos start in **This subtree** scope, which includes child experiments.
  Use **This record** for one record or the source scope for all attributed
  runs in the selected tracking source. Press **Refresh** to fetch new results.
- **Plots:** Choose **Progress** to plot a final metric on the Y axis against
  run timestamps or run order on X. Choose **Pareto** to compare two metrics
  using separate X/Y selectors and minimize/maximize goals. Each point is
  one run; epoch histories and training artifacts remain in the tracker.
- **Create record:** Add a task or child experiment. **Preview** checks your
  draft; **Create** writes it to the demo project.
- **Launch run:** Select an entrypoint and adjust its arguments. Use
  **Preview launch** to review the command, then **Launch run** to execute it.
  Inspect job status and logs here; completed jobs trigger a results refresh.

Keep the terminal running while using the GUI. Press **Ctrl-C** to stop the
servers; the demo files and results remain in the printed directory. Reopen
that project without repeating the initial experiments:

```bash
uv run --all-extras python scripts/use_case_dashboard.py --reopen "<demo-path>"
```

See the [dashboard demo guide](scripts/README-use-case-dashboard.md) for
requirements and options, and the [GUI manual](docs/guides/dashboard-manual.md)
for a detailed walkthrough.

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
