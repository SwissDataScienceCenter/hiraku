# Essential concepts

| Term | What it represents |
| --- | --- |
| Project | A directory identified by `hiraku.yml`. By default, project data lives under `hiraku/`. |
| Record | A Markdown document with YAML metadata. Use a task record for a research goal and an experiment record for one approach to that goal. |
| Job | One command execution. Hiraku can run it in the foreground or submit it to an executor. |
| Leaderboard | A named configuration that reads runs from CSV, MLflow, or Weights & Biases and optionally defines which metrics to compare and how to sort them. |
| Workflow | A project-defined LangGraph graph that coordinates steps and can checkpoint progress between calls. |

## How records connect to leaderboards

A record ID is its path relative to the records directory, without `.md`. For example, `hiraku/records/classification/baseline.md` has ID `classification/baseline`. Use `--parent` when creating a child record to declare the relationship. A record can also store the Git commit for its implementation.

When you launch a command with `--record classification/baseline`, Hiraku supplies `HIRAKU_RECORD_ID` and `HIRAKU_JOB_ID`. Your script logs those values with its results. A leaderboard reads the resulting runs and lets you compare experiments; a job can log several runs or finish without logging any.

Follow the [Getting started walkthrough](getting_started.md#run-your-first-experiment) to see those pieces work together.
