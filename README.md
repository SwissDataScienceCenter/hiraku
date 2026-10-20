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

# create a hiraku project (Git is optional)
hiraku init my-project
cd my-project
```

Check the examples for a more complete walkthrough:

- [Heart-disease prediction using CSV logging](docs/examples/heart-disease/README.md)
- [Iris dataset using Weights & Biases](docs/examples/iris-wandb/README.md)
- [Digits dataset using MLFlow](docs/examples/digits-mlflow/README.md)
- [Karpathy's LLM autoresearch example](docs/examples/autoresearch/README.md)

Initialize an example in any of these ways:

| Command | Result |
| --- | --- |
| `hiraku clone NAME` | Copy the runnable project files only |
| `hiraku clone NAME --with-results` | Also copy bundled records and measured runs |
| `hiraku clone NAME --with-generator` | Also copy `generate_results.py` for running later |
| `hiraku clone NAME --generate` | Generate fresh records and runs immediately |

`hiraku init [PATH]` initializes the current or explicit directory, optionally
using a local `--template`. `hiraku clone NAME [PATH]` creates a project from a
bundled example, without Git history or network access. Its default destination
is `./NAME`. Use
`hiraku clone NAME .` to initialize the current directory, or
`hiraku clone NAME my-project` to choose another destination.
Initialization does not require Git. Add `--git` to initialize the code repository
as a convenience. Repeating plain `hiraku init` leaves an initialized project
unchanged; repeating it with `--git` can add the code repository later.

Optional Git setup is available at any time:

```bash
hiraku git setup ignore  # ignore Hiraku files in the code repository
hiraku git setup repo    # independent local Git history inside hiraku/
hiraku git setup submodule --url PATH_OR_URL
hiraku git status
```

`repo` and `submodule` keep research history separate from code history. The
metadata repository must already have a commit before submodule registration;
`--url` records its future cloning location, including a local repository path.
Setup preserves existing files and history and never commits or pushes. See the
[Git setup guide](docs/guides/git.md) for examples and migration requirements.

The results and generator modes are available for the Heart Disease and Digits
examples. `--generate` cannot be combined with `--with-results`; generation can
take time and may install the example's dependencies.

## Dashboard

`hiraku dashboard` serves a local [marimo](https://marimo.io) notebook through a [marimo-studio](https://marimo-team.github.io/marimo-studio/) view with the record tree, the runs attributed to each record, run details with links to the tracking backend, and a performance plot. It needs the `dashboard` extra.

| Command | What it does |
| --- | --- |
| `hiraku dashboard` | Serve the read-only dashboard at <http://127.0.0.1:8765> |
| `hiraku dashboard --include-code` | Also let the page show its notebook code (marimo menu, **⋯**) |
| `hiraku dashboard --init` | Copy the notebook to `hiraku/dashboard/app.py` so you can customize it |
| `hiraku dashboard --edit` | Open that copy in marimo-studio |
| `hiraku dashboard --init --edit` | Both in one step |

After `--init`, `hiraku dashboard` serves your copy. In the editor, the
`runs_df` and `records_df` tables hold every run and record with its place in
the tree, ready for your own plots or marimo's AI assistant.

Try every dashboard mode without training by loading the bundled Digits runs:

```bash
hiraku clone digits-mlflow digits-dashboard --with-results
cd digits-dashboard
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
