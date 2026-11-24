# Hiraku concepts

| Term | What it represents |
| --- | --- |
| [Project](guides/index.md) | A directory identified by `hiraku.yml`. By default, project data lives under `hiraku/`. |
| [Record](guides/experiment-branches.md) | A Markdown document with YAML metadata. Use a task record for a research goal and an experiment record for one approach to that goal. |
| [Job](guides/jobs.md) | One command execution. Hiraku can run it in the foreground or submit it to an executor. |
| [Leaderboard](tracking.md) | A named configuration that reads runs from CSV, MLflow, or Weights & Biases and optionally defines which metrics to compare and how to sort them. |
| [Workflow](guides/workflows.md) | A project-defined LangGraph graph that coordinates steps and can checkpoint progress between calls. |

## Overview: How it fits together

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

## How records connect to leaderboards

A record ID is its path relative to the records directory, without `.md`. For example, `hiraku/records/classification/baseline.md` has ID `classification/baseline`. Use `--parent` when creating a child record to declare the relationship. A record can also store the Git commit for its implementation.

When you launch a command with `--record classification/baseline`, Hiraku supplies `HIRAKU_RECORD_ID` and `HIRAKU_JOB_ID`. Your script logs those values with its results. A leaderboard reads the resulting runs and lets you compare experiments; a job can log several runs or finish without logging any.

Follow the [First experiment walkthrough](first_experiment.md) to see those pieces work together.
