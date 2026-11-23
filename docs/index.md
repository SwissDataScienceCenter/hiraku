<div class="sdsc-home-hero" markdown="1">

# Hiraku

Document research in Markdown, launch experiments, and compare results across runs.

```bash
uv tool install 'git+https://github.com/SwissDataScienceCenter/hiraku.git'
```

[Getting started](getting_started.md){ .md-button .md-button--primary }
[Installation options](getting_started.md#install-hiraku){ .md-button }

</div>

## How Hiraku fits together

<figure class="architecture-preview" aria-label="Hiraku architecture">
  <section class="development">
    <h3>User-driven development</h3>
    <p>Using any coding agent</p>
    <div class="items"><span>CLI</span><span>MCP</span><span>Dashboard</span></div>
  </section>
  <section class="workflows">
    <h3>Customizable agentic workflows</h3>
    <p>Using LangGraph</p>
    <div class="items"><span>Workflow A</span><span>Workflow B</span><span>Workflow C</span></div>
  </section>
  <section class="records">
    <h3>Hiraku research records</h3>
    <p>Using the Open Knowledge Format</p>
    <div class="items"><span>Datasets</span><span>Tasks</span><span>Experiments</span><span>Analysis</span><span>Results</span></div>
  </section>
  <section class="jobs">
    <h3>Job submission</h3>
    <p>Using PSI/J</p>
    <div class="items"><span>Local</span><span>Renku</span><span>Slurm</span></div>
  </section>
  <section class="leaderboards">
    <h3>Leaderboards</h3>
    <p>Via tracking backends</p>
    <div class="items"><span>CSV</span><span>MLflow</span><span>W&amp;B</span></div>
  </section>
</figure>

## Demos

<div class="sdsc-home-demos" markdown="1">

- [First experiment](getting_started.md#run-your-first-experiment)

    Run a baseline with two seeds and compare both results on a CSV leaderboard.

- [Digits](examples/digits/README.md)

    Track digit-classification runs and metrics with MLflow.

- [OGB Hiraku](https://github.com/SwissDataScienceCenter/ogb-hiraku)

    Explore the Open Graph Benchmark example project.

</div>

[Use Hiraku in your own project](guides/index.md)
