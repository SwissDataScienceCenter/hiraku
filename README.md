# Hiraku

**Hiraku** is a toolkit for AI-assisted data science. It provides customizable workflows and a shared metadata record around the model development lifecycle of data science projects.

> **Why Hiraku?** It's a hint towards a possible integration with [renkulab.io](https://renkulab.io). In renku (連句), the traditional Japanese art of collaborative linked poetry, hiraku (平句) are the regular verses where the sequence develops, shifts, and explores new directions — a metaphor for iterative, exploratory research within the Renku ecosystem. *[Discuss here.](https://github.com/SwissDataScienceCenter/hiraku/issues/6)*

## Getting started

```bash
git clone https://github.com/SwissDataScienceCenter/hiraku.git
cd hiraku

# system install
uv tool install ".[mlflow,wandb,dashboard,snakemake]"

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

- [Start from scratch with a minimal project](docs/examples/minimal/README.md)
- [Heart-disease prediction using CSV logging](docs/examples/heart-disease/README.md)
- [Iris dataset using Weights & Biases](docs/examples/iris-wandb/README.md)
- [Digits dataset using MLFlow](docs/examples/digits-mlflow/README.md)
- [Digits regularization variants with Snakemake and MLflow](docs/examples/digits-snakemake/README.md)
- [EuroSAT RGB image classification](docs/examples/eurosat-rgb/README.md)
- [Workflow examples](docs/examples/workflows/README.md)
- [Karpathy's LLM autoresearch example](docs/examples/autoresearch/README.md)

Initialize an example in any of these ways:

| Command | Result |
| --- | --- |
| `hiraku init --example NAME` | Copy the runnable project files only |
| `hiraku init --example NAME --with-results` | Also copy bundled records and measured runs |
| `hiraku init --example NAME --with-generator` | Also copy `generate_results.py` for running later |
| `hiraku init --example NAME --generate` | Generate fresh records and runs immediately |

The results and generator modes are available for the Heart Disease and Digits
examples. `--generate` cannot be combined with `--with-results`; generation can
take time and may install the example's dependencies.

## Snakemake workflows

`hiraku snakemake run` executes a [Snakemake](https://snakemake.github.io) workflow whose steps are record entrypoints, and stores each execution as one run of a workflow record, with one entry per step. The records keep each step's command and arguments; the rules only connect the steps through the files they read and write. It needs the `snakemake` extra.

There are two ways to get the rules:

- **Observe the launches.** Launch each step once with `hiraku launch --observe`; Hiraku notes the files each launch read and wrote and builds the rules from them, so there is no `Snakefile` to write. Observation is opt-in: a plain `hiraku launch` is unchanged.

  ```bash
  hiraku launch prepare --record workflow --observe -- --output data.csv
  hiraku launch train --record model --observe -- --input data.csv --output model.pkl
  hiraku snakemake run --record workflow
  ```

- **Write a `Snakefile`.** It takes over when it exists, and suits agentic work: the rules only wire files to `hiraku launch` of an entrypoint, so an edit cannot change the code a record runs; `hiraku snakemake run --dry-run` checks them before any compute; each run's `jobs` show what actually ran; and a person reviews the change as a diff. `hiraku snakemake generate` drafts the file, with `--observed` from the observed launches.

The dashboard shows each workflow run's steps, and for each file, the step that produced it and the steps that used it. The [Snakemake guide](docs/guides/tracking-snakemake.md) is the reference, the [Digits Snakemake example](docs/examples/digits-snakemake/README.md) is a complete project, and the [Snakemake pipelines](docs/examples/snakemake-pipelines/index.md) pages run every other example both ways, step by step.

## Dashboard

`hiraku dashboard` serves a local [marimo](https://marimo.io) notebook through a [marimo-studio](https://marimo-team.github.io/marimo-studio/) view with the record tree, the runs attributed to each record, run details with links to the tracking backend, a performance plot, and the files of Snakemake workflow runs. It needs the `dashboard` extra.

| Command | What it does |
| --- | --- |
| `hiraku dashboard` | Serve the read-only dashboard at <http://127.0.0.1:8765> |
| `hiraku dashboard --include-code` | Also let the page show its notebook code (marimo menu, **⋯**) |
| `hiraku dashboard --init` | Copy the notebook to `hiraku/dashboard/app.py` so you can customize it |
| `hiraku dashboard --edit` | Open that copy in marimo-studio |
| `hiraku dashboard --init --edit` | Both in one step |

After `--init`, `hiraku dashboard` serves your copy. In the editor, the
`runs_df` and `records_df` tables hold every run and record with its place in
the tree, and `files_df` the files of workflow runs, ready for your own plots or marimo's AI assistant.

Try every dashboard mode without training by loading the bundled Digits runs:

```bash
mkdir digits-dashboard && cd digits-dashboard
git init
hiraku init --example digits-mlflow --with-results
hiraku dashboard --record digits                 # read-only
hiraku dashboard --include-code --record digits  # read-only, with source
hiraku dashboard --init --edit --record digits   # copy and edit
```

Run the dashboard commands one at a time and press Ctrl-C before changing mode.
The bundled database works without an MLflow server. To show **Open in
mlflow** links, start the MLflow UI and configure its `ui_url`; the
[dashboard guide](docs/guides/dashboard.md) has the complete commands.

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
