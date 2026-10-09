<div class="sdsc-hero">
<p class="sdsc-kicker">Swiss Data Science Center · Hiraku</p>

<h1>Research, made traceable</h1>

Hiraku gives data-science teams a small, scriptable loop for defining a goal,
testing an idea, and keeping the evidence.

<a class="md-button" href="examples/heart-disease/">Run your first experiment</a>
</div>

<div class="sdsc-flow">
<div class="sdsc-flow-step">

<h3>Define</h3>

A research question, metric, and evaluation protocol.
</div>
<div class="sdsc-flow-step">

<h3>Experiment</h3>

Small, reproducible units track the research progress.
</div>
<div class="sdsc-flow-step">

<h3>Learn</h3>

Launch entrypoints, compare runs, and keep the research history visible.
</div>
</div>



## Installation

```bash
uv tool install ".[mlflow,wandb]"
hiraku --help

# development setup
uv sync --all-extras
uv run hiraku --help
```


## The Hiraku workflow

Hiraku connects the research loop with records and tracked runs:

```mermaid
flowchart LR
    T[Root record<br/>research goal] --> E[Child record<br/>focused attempt] --> R[Run<br/>metric trace]
```

* A root `Record` may define a research goal, metrics, shared entrypoints, and a tracking backend. It lives at `hiraku/records/{root-id}/{root-id}.md`.
* A child `Record` may focus on an experiment, diagnosis, or review. It inherits through `parent_id` and may bind its own code commit.
* A `Run` records one execution attributed to a `record_id`. Runs are managed by CSV, MLflow, W&B, or another backend and queried by Hiraku.
* Additionally, an `Entrypoint` defines an executable command associated with documented arguments. Hiraku provides launch helpers and customizable dispatch scripts.

??? tip "Branching and experiment lineage"
    Git remains responsible for commits, branches, and source history. 
    Branches are optional for the core workflow.

### Start here

Follow the [heart disease example](examples/heart-disease/README.md) for a complete workflow. 

For a preview of a fully agentic workflow, see the [autoresearch example](examples/autoresearch/README.md).

For more details, see [Essential concepts](concepts.md).

## Documentation

<div class="sdsc-card-grid">
<div class="sdsc-card">

<h3>Examples</h3>

Run complete workflows with CSV, W&B, and MLflow.

<a href="examples/">Browse examples</a>
</div>
<div class="sdsc-card">

<h3>Guides</h3>

Set up a project, choose a tracking backend, or connect an AI agent.

<a href="guides/">Read the guides</a> · <a href="dashboard/">Open the dashboard</a>
</div>
<div class="sdsc-card">

<h3>API</h3>

Look up CLI commands and the Python API when you need exact details.

<a href="api/cli/">CLI</a> · <a href="api/python/">Python API</a>
</div>
</div>
