# Hiraku

```bash
uv tool install hiraku
```

<section class="sdsc-home-hero" aria-labelledby="sdsc-home-title">
<p class="sdsc-home-hero__eyebrow">Swiss Data Science Center · Research tooling</p>
<p id="sdsc-home-title" class="sdsc-home-hero__lead">Keep research questions, code, and results connected.</p>
<p>Document experiments in Markdown, link them to the code you ran, and compare results from CSV, MLflow, or Weights &amp; Biases. Hiraku keeps the research context close to every run.</p>
</section>

For example, establish a baseline by creating an experiment record and running the same evaluation with two seeds. Both results appear under the same record, where you can review the scores and write down what you learned.

<figure class="architecture-preview" aria-label="Hiraku architecture">
  <section class="development">
    <h2>User-driven development</h2>
    <p>Using any coding agent</p>
    <div class="items"><span>CLI</span><span>MCP</span><span>Dashboard</span></div>
  </section>
  <section class="workflows">
    <h2>Customizable agentic workflows</h2>
    <p>Using LangGraph</p>
    <div class="items"><span>Workflow A</span><span>Workflow B</span><span>Workflow C</span></div>
  </section>
  <section class="records">
    <h2>Hiraku research records</h2>
    <p>Using the Open Knowledge Format</p>
    <div class="items"><span>Datasets</span><span>Tasks</span><span>Experiments</span><span>Analysis</span><span>Results</span></div>
  </section>
  <section class="jobs">
    <h2>Job submission</h2>
    <p>Using PSI/J</p>
    <div class="items"><span>Local</span><span>Renku</span><span>Slurm</span></div>
  </section>
  <section class="leaderboards">
    <h2>Leaderboards</h2>
    <p>Via tracking backends</p>
    <div class="items"><span>CSV</span><span>MLflow</span><span>W&amp;B</span></div>
  </section>
</figure>

Research records document the data science lifecycle. Researchers and coding agents can read and update them through the CLI, MCP server, and dashboard.

For other installation options, see the [installation instructions](getting_started.md#install-hiraku). Then:

1. [Run your first experiment](getting_started.md#run-your-first-experiment) with a small CSV-tracked evaluation.
2. [Use Hiraku in your own project](guides/index.md).
