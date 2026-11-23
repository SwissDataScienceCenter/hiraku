# Hiraku

```bash
uv tool install hiraku
```

Hiraku connects the question behind an experiment, the code you ran, and the results you measured.

Write the question and your conclusions in Markdown, associate the experiment with a Git commit, and query its results from CSV, MLflow, or Weights & Biases.

For example, to establish a baseline, create an experiment record and run the same evaluation with two seeds. Both results appear under the same record, where you can review the scores and write down what you learned.

<style> .md-typeset figure.architecture-preview { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1.1rem .5rem; margin: 1.5rem 0; width: 100%; } .architecture-preview section { position: relative; padding: .55rem; border: 1px solid #7778; border-radius: .65rem; background: #eee; color: #20252b; text-align: center; } .architecture-preview .development, .architecture-preview .workflows { background: #dcedd8; } .architecture-preview .jobs, .architecture-preview .leaderboards { background: #d9e9f6; } .architecture-preview h2 { margin: 0; color: inherit; font-size: .85rem; font-weight: 700; } .architecture-preview p { margin: .15rem 0 0; font-size: .65rem; } .architecture-preview .items { display: flex; flex-wrap: wrap; gap: .25rem; margin-top: .4rem; } .architecture-preview .items span { flex: 1 0 auto; padding: .15rem .25rem; border: 1px solid #7778; border-radius: .2rem; background: #fff; font-size: .68rem; } @media (min-width: 760px) { .architecture-preview .records { grid-column: 1 / -1; } .architecture-preview .development::after, .architecture-preview .workflows::after, .architecture-preview .jobs::before, .architecture-preview .leaderboards::before { position: absolute; left: 50%; transform: translateX(-50%); color: var(--md-default-fg-color); line-height: 1.1rem; } .architecture-preview .development::after, .architecture-preview .workflows::after { top: 100%; content: "↕" / ""; } .architecture-preview .jobs::before, .architecture-preview .leaderboards::before { bottom: 100%; content: "↑" / ""; } } @media (max-width: 759px) { .md-typeset figure.architecture-preview { grid-template-columns: minmax(0, 1fr); gap: .5rem; } } </style>

<figure class="architecture-preview" aria-label="Hiraku architecture"> <section class="development"> <h2>User-driven development</h2> <p>Using any coding agent</p> <div class="items"><span>CLI</span><span>MCP</span><span>Dashboard</span></div> </section> <section class="workflows"> <h2>Customizable agentic workflows</h2> <p>Using LangGraph</p> <div class="items"><span>Workflow A</span><span>Workflow B</span><span>Workflow C</span></div> </section> <section class="records"> <h2>Hiraku research records</h2> <p>Using the Open Knowledge Format</p> <div class="items"><span>Datasets</span><span>Tasks</span><span>Experiments</span><span>Analysis</span><span>Results</span></div> </section> <section class="jobs"> <h2>Job submission</h2> <p>Using PSI/J</p> <div class="items"><span>Local</span><span>Renku</span><span>Slurm</span></div> </section> <section class="leaderboards"> <h2>Leaderboards</h2> <p>Via tracking backends</p> <div class="items"><span>CSV</span><span>MLflow</span><span>W&B</span></div> </section> </figure>

Research records document the data science lifecycle. Researchers and workflows read and update them, while jobs run computations and leaderboards surface results from tracking backends.

For other installation options, see the [installation instructions](getting_started.md#install-hiraku). Then:

1. [Run your first experiment](examples/minimal/README.md) using a bundled evaluation script and local CSV tracking.
2. [Use Hiraku in your own project](guides/index.md).
