# Hiraku

[![Latest release][release-badge]](https://swissdatasciencecenter.github.io/hiraku/about/changelog/)
[![Documentation](https://img.shields.io/badge/docs-read-2563EB)](https://swissdatasciencecenter.github.io/hiraku/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://swissdatasciencecenter.github.io/hiraku/installation/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue)](https://swissdatasciencecenter.github.io/hiraku/about/license/)

## Collaborative data science, with agents.

Hiraku is built on a simple premise: **effective collaboration requires a shared structure.**

Designed to support **user-driven development and scalable agentic workflows**, Hiraku provides a standardized workspace for human-agent collaboration.
Built on the **open ecosystem**, Hiraku integrates with existing research infrastructure and adapts to *your* project needs.

## Get started

Requires Python 3.11 or newer and [uv](https://docs.astral.sh/uv/getting-started/installation/).
Install Hiraku and initialize a project.

```bash
uv tool install hiraku
hiraku --help
```

To initialize Hiraku on a new or existing project, run:
```bash
cd my-project
hiraku init .
```
See the [installation guide](https://swissdatasciencecenter.github.io/hiraku/installation/) for
optional dependencies and other ways to install Hiraku.

Follow the [first experiment walkthrough](https://swissdatasciencecenter.github.io/hiraku/first_experiment/) to record a
research goal, evaluate a baseline twice, and compare the runs.

## Features

Human and agents operate on a shared structure and interface. Hiraku provides:

- **[Projects](https://swissdatasciencecenter.github.io/hiraku/guides/)** — initialize Hiraku in a new or existing
  project, with configuration in `hiraku.yml` and project data under `hiraku/`
  by default.
- **[Research records](https://swissdatasciencecenter.github.io/hiraku/guides/experiment-branches/)** — document research
  tasks, experiments, and results in Markdown with YAML metadata, following the [Open Knowledge Format](https://github.com/open-knowledge-format/open-knowledge-format). Connect
  related records, runs and code behind each experiment.
- **[Jobs](https://swissdatasciencecenter.github.io/hiraku/guides/jobs/)** — run commands or submit
  them to PSI/J executors, including local, Renku, and Slurm execution. Pass
  record and job IDs to your scripts so they can attribute results.
- **[Leaderboards](https://swissdatasciencecenter.github.io/hiraku/tracking/)** — read runs from CSV, MLflow, or
  Weights & Biases, choose comparison metrics, and review results across
  experiments.
- **[Workflows](https://swissdatasciencecenter.github.io/hiraku/guides/workflows/)** — define project-specific LangGraph
  graphs to coordinate steps, with session history and checkpoints to preserve
  progress between calls.

## Examples

Browse the [Digits example](https://swissdatasciencecenter.github.io/hiraku/examples/digits/) to see how Hiraku
tracks model runs and metrics with MLflow. Copy the example with its prepared
results to try it locally.

```bash
hiraku example create digits --with-results
```

Explore a larger project in the
[Open Graph Benchmark demo](https://github.com/SwissDataScienceCenter/ogb-hiraku).

## Documentation

Read the [Hiraku documentation](https://swissdatasciencecenter.github.io/hiraku/)
for [core concepts](https://swissdatasciencecenter.github.io/hiraku/concepts/), [user guides](https://swissdatasciencecenter.github.io/hiraku/guides/), and
the [Python API reference](https://swissdatasciencecenter.github.io/hiraku/api/). The guides cover
[leaderboards](https://swissdatasciencecenter.github.io/hiraku/tracking/), the [dashboard](https://swissdatasciencecenter.github.io/hiraku/guides/dashboard/),
[MCP](https://swissdatasciencecenter.github.io/hiraku/guides/mcp/), and [workflows](https://swissdatasciencecenter.github.io/hiraku/guides/workflows/).

## Contributing

See the [contributor guide](https://swissdatasciencecenter.github.io/hiraku/about/contributing/) for development setup and checks.
Report bugs and propose features through the
[GitHub issue tracker](https://github.com/SwissDataScienceCenter/hiraku/issues).

[release-badge]: https://img.shields.io/badge/dynamic/regex?url=https%3A%2F%2Fswissdatasciencecenter.github.io%2Fhiraku%2Fabout%2Fchangelog%2F&search=%3Ch2%5Cb%5B%5E%3E%5D%2A%3E%5Cs%2A%5C%5B%28%5B0-9%5D%2B%5C.%5B0-9%5D%2B%5C.%5B0-9%5D%2B%29%5C%5D&replace=v%241&label=release&color=2EA44F
