# Essential concepts

| Term | What it represents |
| --- | --- |
| Project | A directory identified by `hiraku.yml`. By default, project data lives under `hiraku/`. |
| Record | A Markdown document with YAML metadata. Use a task record for a research goal and an experiment record for one approach to that goal. |
| Job | One command execution. Hiraku can run it in the foreground or submit it to an executor. |
| Run | A result logged by your script to CSV, MLflow, or Weights & Biases. It can include metrics, parameters, and record and job IDs. |
| Leaderboard | A named configuration that selects a run source and optionally defines which metrics to compare and how to sort them. |

## How records connect to runs

A record ID is its path relative to the records directory, without `.md`. For example, `hiraku/records/classification/baseline.md` has ID `classification/baseline`. Use `--parent` when creating a child record to declare the relationship. A record can also store the Git commit for its implementation.

When you launch a command with `--record classification/baseline`, Hiraku supplies `HIRAKU_RECORD_ID` and `HIRAKU_JOB_ID`. Your script logs those values with its results so you can look up the runs for that experiment. A job can log several runs or finish without logging any.

Follow the [first experiment tutorial](examples/minimal/README.md) to see those pieces work together.
