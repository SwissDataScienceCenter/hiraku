
### MLflow tracking backend

MLflow is an optional installation extra. It uses a local SQLite database at
`<project-root>/mlflow.db` by default; a safe project-relative `mlflow_tracking_db` overrides it.
Alternatively, `mlflow_tracking_uri` selects a remote HTTP(S) tracking server. The local override and
remote URI are mutually exclusive. Each task specifies `mlflow_experiment` in `task.md` front matter.

```yaml
tracking_backend: mlflow
mlflow_tracking_db: mlflow.db  # optional
```

For remote tracking, omit `mlflow_tracking_db`:

```yaml
tracking_backend: mlflow
mlflow_tracking_uri: https://mlflow.example.org
```

Hiraku passes a remote URI unchanged to launches and run inspection. Authentication and TLS use
standard MLflow environment variables; credentials must not be embedded in `.hiraku.yml`. Remote
errors never fall back to local SQLite. Hiraku does not provision or administer the server.

Hiraku constructs an absolute `sqlite:///` tracking URI for the database under the original project root. Committed launches therefore write to the same database even though their source executes from a detached temporary worktree.

Hiraku uses MLflow's native run ID and start time as `run_id` and `created_at`. User code records `task_id`, semantic `experiment_id`, and `commit` as MLflow parameters. JSON keeps `params`, `metrics`, and `tags` namespaced. For a metric logged several times, run inspection uses MLflow's returned latest-timestamp value; metric history is not part of Stage 2. By default, listings show only runs with all required provenance and a matching task ID, while warning about omitted runs. The MLflow-only `--all` option reveals invalid, wrong-task, or unassociated runs with explicit warnings and leaves runs without numeric sort values unranked.

MLflow launches receive `HIRAKU_MLFLOW_TRACKING_URI`, `HIRAKU_MLFLOW_EXPERIMENT`, `MLFLOW_TRACKING_URI`, and `MLFLOW_EXPERIMENT_NAME` instead of `HIRAKU_TABLE_PATH`. Both backends continue to receive the common task, experiment, commit, and backend variables.

