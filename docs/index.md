

<div class="sdsc-home-hero" markdown="1">
# Hiraku

<h2 id="home-subtitle" class="sdsc-home-hero-subtitle">Collaborative data science, with agents.</h2>

Hiraku is built on a simple premise: **effective collaboration requires a shared structure.**

Designed to support **user-driven development and scalable agentic workflows**, Hiraku provides a customizable workspace for human-agent collaboration.
Built on the **open ecosystem**, Hiraku integrates with existing research infrastructure and adapts to *your* project needs.


```bash
uv tool install hiraku
```
[Get started](quickstart/first_experiment.md){ .md-button .md-button--primary }
[Install options](quickstart/installation.md){ .md-button }
[Explore concepts](quickstart/concepts.md){ .md-button }

!!! warning "Beta release"

    Hiraku is in active development. Features and interfaces may change between releases. 
    
    Please provide feedback and report issues on [GitHub](https://github.com/SwissDataScienceCenter/hiraku/issues).
</div>

## Features

Hiraku is a toolkit designed to integrate agentic capabilities into data science projects. It establishes shared conventions and interfaces through which humans and agents can carry out research workflows and share knowledge.

- **Common interfaces:** Humans and agents interact with Hiraku through a common interface accessible via the CLI, MCP, and Python API.
- **Shared knowledge base:** Markdown records document research progress and serve as a shared documentation layer for humans and agents, following the [Open Knowledge Format](https://github.com/open-knowledge-format/open-knowledge-format).
- **Job submission:** Hiraku integrates local and HPC backends (e.g. Slurm) for job execution.
- **Tracking leaderboards:** Hiraku retrieves experiment runs directly from user-managed tracking backends such as MLflow and Weights & Biases.
- **User-driven development:** Implement your own ideas, follow up on agent experiments, a drive the development of your projects [using your preferred coding agent](quickstart/agentic_workflows.md).
- **Agentic workflows:** Hiraku supports agentic workflows through LangGraph, which allows users to define project-specific graphs for agentic execution.
- **Code and data access:** Humans and agents access code and data directly, using standard tools such as version-controlled repositories.

## Explore further

<div class="sdsc-home-demos" markdown="1">

- [Examples](examples/index.md)

    Get started with ready-to-run examples.

- [User Guides](guides/index.md)

    Learn about Hiraku concepts and how to use them.

- [Demo Project](https://github.com/SwissDataScienceCenter/ogb-hiraku)

    Explore a Hiraku demo project on the Open Graph Benchmark<sup>[↗](https://ogb.stanford.edu/)</sup>.

</div>
