# Hiraku

[![Latest release][release-badge]](CHANGELOG.md)
[![Documentation](https://img.shields.io/badge/docs-read-2563EB)](https://swissdatasciencecenter.github.io/hiraku/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](docs/installation.md)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

## Collaborative data science

Hiraku gives researchers and coding agents a shared structure for developing
models and evaluating results. Keep research goals, experiments, and findings
in Markdown records. Launch jobs against those records and compare the
resulting runs with leaderboards that read from CSV, MLflow, or Weights &
Biases.

Use the CLI to work through an experiment, connect a coding agent through
MCP, or define a repeatable procedure with LangGraph.

## Get started

Use Python 3.11 or newer and [uv](https://docs.astral.sh/uv/getting-started/installation/).
Install Hiraku and initialize a project.

```bash
uv tool install hiraku
hiraku init my-project
cd my-project
```

Initialization creates `hiraku.yml`. Add `--git` to `hiraku init` to initialize
a Git repository. See the [installation guide](docs/installation.md) for
optional dependencies and other ways to install Hiraku.

Follow the [first experiment walkthrough](docs/first_experiment.md) to record a
research goal, evaluate a baseline twice, and compare the runs.

## Examples

Browse the [Digits example](docs/examples/digits/README.md) to see how Hiraku
tracks model runs and metrics with MLflow. Copy the example with its prepared
results to try it locally.

```bash
hiraku example create digits --with-results
```

Explore a larger project in the
[Open Graph Benchmark demo](https://github.com/SwissDataScienceCenter/ogb-hiraku).

## Documentation

Read the [Hiraku documentation](https://swissdatasciencecenter.github.io/hiraku/)
for [core concepts](docs/concepts.md), [user guides](docs/guides/index.md), and
the [Python API reference](docs/api/index.md). The guides cover
[leaderboards](docs/tracking.md), the [dashboard](docs/guides/dashboard.md),
[MCP](docs/guides/mcp.md), and [workflows](docs/guides/workflows.md).

For command-line options, run `hiraku --help` or a command's own help, such as
`hiraku record create --help`.

## Contributing

See the [contributor guide](CONTRIBUTING.md) for development setup and checks.
Report bugs and propose features through the
[GitHub issue tracker](https://github.com/SwissDataScienceCenter/hiraku/issues).

[release-badge]: https://img.shields.io/badge/dynamic/regex?url=https%3A%2F%2Fswissdatasciencecenter.github.io%2Fhiraku%2Fabout%2Fchangelog%2F&search=%3Ch2%5Cb%5B%5E%3E%5D%2A%3E%5Cs%2A%5C%5B%28%5B0-9%5D%2B%5C.%5B0-9%5D%2B%5C.%5B0-9%5D%2B%29%5C%5D&replace=v%241&label=release&color=2EA44F
