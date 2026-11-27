# Overview

A Hiraku project is directory identified by a `hiraku.yml` configuration file. By default, Hiraku data is stored in `hiraku/`.

Hiraku connects four core components:

| Term | What it represents |
| --- | --- |
| [Record](../guides/records.md) |  A Markdown document with YAML metadata, following the [Open Knowledge Format](https://github.com/open-knowledge-format/open-knowledge-format). |
| [Job](../guides/jobs.md) | A single command execution delegated to a selected compute backend. Hiraku provides status and history of job submissions. |
| [Leaderboard](../guides/leaderboards.md) | A named configuration for retrieving and comparing runs from CSV, MLflow, or Weights & Biases. |
| [Workflow](../guides/workflows.md) | A LangGraph graph that orchestrates multiple steps and can checkpoint progress between calls.  |

## Project subagents

`hiraku init` installs four research subagents for Codex and Claude Code:
literature review, hypothesis design, experiment implementation, and results
analysis. The bundled Hiraku skill routes matching work to these specialists
when the host supports subagent delegation. Existing agent files are preserved
so local edits survive project initialization.

## Architecture


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

