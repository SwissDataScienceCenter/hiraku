# Track with CSV

Create a leaderboard that reads a CSV file:

```bash
hiraku leaderboard create local --source csv --path runs.csv
```

Relative paths resolve from the project root. Use a quoted reference such as `'${MY_RESULTS_DIR}/runs.csv'` to configure the location through an environment variable. Set the variable before querying runs.

## Log from your script

Append one row per run. The `id` column is required and should be unique. Use `metric.` for numeric results and `param.` for inputs such as the seed.

```csv
id,record_id,hiraku_job_id,timestamp,metric.val/accuracy,param.seed
run-001,baseline,JOB_ID,2026-01-01T12:00:00Z,0.84,42
```

Read `record_id` and `hiraku_job_id` from the `HIRAKU_RECORD_ID` and `HIRAKU_JOB_ID` environment variables. The optional `commit` column can hold the commit reported by your script. See the [First experiment walkthrough](../first_experiment.md) for a working CSV example.

## Inspect results

```bash
hiraku leaderboard runs --leaderboard local --sort val/accuracy
```

The sort uses the metric name without the `metric.` prefix. See [Leaderboards](../tracking.md) for metric directions and filters.
