

# Hiraku CLI

> This section defines the intended CLI shape. The CLI directly invokes the core Python API.

Tasks are managed explicitly. Experiments are semantic records in the local task registry; each implemented experiment binds to one source commit for provenance and lineage.

### Initialize a project

```bash
hiraku init
```

This creates `hiraku.yml`, the metadata directories under `hiraku/`, editable
bundled programs and actions, the default dispatcher, and the repository-local
agent skill `.agents/skills/hiraku/`. Active selections initially remain unset.

### Create and inspect tasks

```bash
hiraku task create heart-disease
hiraku task create heart-disease-search --program autoresearch
hiraku task create custom-workflow --program custom --no-set
hiraku task set heart-disease
hiraku task update heart-disease --summary "Improved baseline study"
hiraku task  # compact metadata for the current task
hiraku task show heart-disease
hiraku task show heart-disease --verbose
hiraku task show heart-disease --json
hiraku task entrypoints
hiraku task entrypoints heart-disease --json
```

New tasks default to the `interactive` program and canonical branch `hiraku/task/<task-id>`. The selected program is recorded in `task.md`. The branch is derived from the task ID, recorded in `task.md`, and cannot be set arbitrarily. Task front matter also contains a required one-line `summary`. Stage 2 uses it as the review title and records the persistent provider identity as optional `pr_id` for GitHub or `mr_id` for GitLab. Task creation sets the new task as active unless `--no-set` is supplied. It does not switch Git branches; it creates the task files and prints instructions for switching to the task branch. Creation does not require a clean worktree.
The Hiraku skill checks that implementation work occurs on the task branch. It switches branches only when directed by the user, except that it may switch automatically when the working tree is clean and doing so is consistent with the user's instructions.

`hiraku task` is shorthand for showing the task inferred from the current task branch. Task display is compact by default and includes only front-matter metadata; `--verbose` also renders the Markdown body. JSON follows the same rule and includes `body` only with `--verbose`. `hiraku task entrypoints [task]` lists the named entrypoints defined in the selected task record.

### Create and inspect experiments

```bash
hiraku experiment create heart-disease:logistic-baseline --summary "Logistic regression baseline" --entrypoint logistic
hiraku experiment create heart-disease:random-forest --summary "Random forest comparison" --entrypoint random-forest
hiraku experiment list
hiraku experiment list heart-disease
hiraku experiment show heart-disease:logistic-baseline
hiraku experiment set heart-disease:logistic-baseline
hiraku experiment update heart-disease:logistic-baseline --commit HEAD
hiraku experiment delete heart-disease:logistic-baseline
hiraku experiment create heart-disease:stronger-regularization --branch --from HEAD --no-set
```

`hiraku experiment create` sets the experiment and its task as active unless `--no-set` is supplied. `set` changes the active experiment context; `update` modifies an experiment record. `hiraku exp` is a shorthand for `hiraku experiment`. Every record is Markdown with YAML front matter, and direct editing is supported. `list` without a task resolves to the current task, and its human-readable table includes both the experiment summary and hypothesis.

### Create and inspect programs

```bash
hiraku program create my-program
hiraku program create review-loop --no-set
hiraku program list
hiraku program show interactive
hiraku program set autoresearch
hiraku program update my-program --summary "Custom bounded workflow"
hiraku program delete my-program
```

Programs are Markdown records under `.hiraku/programs/`. `create` sets the new program as active unless `--no-set` is supplied. `set` changes the active program context; `update` modifies the program record. The bundled `interactive` and `autoresearch` programs follow the same data model as user-defined programs.

### Launch named entrypoints

Tasks and experiments may each define named entrypoints in their records. For example, a task may define a shared evaluation command:

```yaml
entrypoints:
  evaluate:
    command: ["uv", "run", "python", "evaluate.py"]
    args:
      seed: 42
    positionals: []
    cwd: "."
```

An experiment may independently define a model-specific training command using the same structure. Task and experiment entrypoints are complementary and are not merged. The launch target selects which scope to use:

```bash
hiraku launch evaluate                                      # task entrypoint
hiraku launch evaluate --experiment logistic-baseline       # task entrypoint for an experiment
hiraku launch logistic-baseline:train                        # experiment entrypoint
hiraku launch evaluate --task heart-disease                  # entrypoint in an explicit task
```

The task is selected by `--task`, then `HIRAKU_TASK_ID`, the local context, and finally the current task branch. The experiment context is selected by `--experiment`, then by the experiment in an `<experiment-id>:<entrypoint>` target, `HIRAKU_EXPERIMENT_ID`, and finally the local context. Explicit options are always overrides. This means a task entrypoint can run for an experiment, and an experiment entrypoint can be reused in a different experiment context. The selected experiment supplies the bound commit and receives the resulting run attribution. Without an experiment context, a task entrypoint runs without experiment attribution.

The positional target still determines where the entrypoint definition comes from: `<entrypoint>` resolves only task entrypoints, while `<experiment-id>:<entrypoint>` resolves only entrypoints defined by that experiment. Hiraku does not merge the namespaces or fall back between them, and both scopes may use the same name.

Launch the current repository state directly as a non-reproducible debugging execution:

```bash
hiraku launch logistic-baseline:train
```

For a reproducible launch, execute an exact commit in an isolated temporary Git worktree:

```bash
hiraku launch --commit HEAD logistic-baseline:train
hiraku launch --commit <commit-sha> logistic-baseline:train
```

Flags after the entrypoint are appended to the fixed command. E.g.,

```bash
hiraku launch logistic-baseline:train --seed 7
```

`hiraku launch`:

1. resolves the task, optional experiment context, and tracking configuration;
2. runs the current working tree as a non-reproducible source unless `--commit` is supplied;
3. for `--commit`, resolves the ref and creates a temporary detached worktree containing the exact committed snapshot;
4. resolves the selected task or experiment entrypoint, fixed argv, safe project-relative working directory, and environment from the selected source tree;
5. injects the task ID, optional experiment ID, source commit, launch provenance, backend type, and fixed destination `tasks/<task-id>/runs.csv` in the original project into the child environment;
6. executes the command directly without a shell and propagates its exit status; and
7. removes a temporary worktree without changing the active checkout.

The integration environment includes:

```text
HIRAKU_TASK_ID
HIRAKU_EXPERIMENT_ID  # set when the launch has an experiment context
HIRAKU_SOURCE_COMMIT
HIRAKU_TRACKING_BACKEND
HIRAKU_TABLE_PATH
```

When an experiment context is selected, `HIRAKU_EXPERIMENT_ID` contains its stable experiment slug. `HIRAKU_SOURCE_COMMIT` contains the resolved implementation SHA. A reproducible experiment launch uses the selected experiment's bound source commit; an explicitly supplied `--commit` must resolve to the same SHA. Working-tree launches remain available for debugging but are non-reproducible and do not establish a bound experiment. Hiraku deliberately does not require a row to appear after a successful child process; the launcher remains a thin local wrapper rather than a scheduler or tracking SDK.

The launcher is a thin, customizable, local command wrapper, not a scheduler or job-dispatch system.

### Inspect experiments, runs, and leaderboards

```bash
hiraku experiment list
hiraku experiment list heart-disease
hiraku runs
hiraku runs heart-disease
hiraku runs heart-disease --sort val/log_loss --ascending
hiraku runs heart-disease --sort val/accuracy --descending
hiraku runs heart-disease --metrics val/log_loss,val/accuracy
hiraku runs heart-disease --verbose
hiraku runs heart-disease --json
hiraku runs heart-disease --all  # MLflow only
```

Experiment and run listings are always scoped to one task, supplied explicitly or inferred from the current task branch. Cross-task listing is not part of the PoC. Experiment listings read the local YAML and Markdown registry and enrich each record with derived launches, runs, metrics, and Git parents. The human experiment table includes each recorded branch. A `*` prefix marks experiments whose exact bound commit is contained in the task branch, followed by a legend naming that branch. This ancestry marker is a Git integration fact kept separate from the semantic `decision`; an accepted experiment without the marker emits a warning. Unbound, missing, squashed, or cherry-picked commits receive no marker because exact ancestry cannot establish their integration.

The default human run table is a compact review view. It shows abbreviated run and commit IDs, experiment, timestamp, status when available, useful MLflow parameters, and selected metrics. It omits the redundant task ID, nested backend objects, MLflow system tags, and a false commit-mismatch field. `--verbose` shows complete flattened metadata without duplicating the nested `params`, `metrics`, and `tags` objects. Human-readable commit hashes use ten characters throughout the CLI; stored records and JSON retain exact hashes.

Metric columns come from the union of metric keys in the displayed runs. MLflow uses only native MLflow metrics; numeric parameters and tags are never inferred as metrics. For table tracking, non-provenance columns are result columns because the format does not distinguish parameters from metrics. `--metrics name,other` explicitly selects and orders human table columns. When task front matter declares `metrics`, those metrics are the default columns in declaration order and the first is the primary ranking metric. Its `minimize` or `maximize` direction supplies the default ranking direction. An explicit `--sort` may omit `--ascending` or `--descending` only when that metric has a declared direction; explicit direction flags override the declaration. Without declared or explicitly selected metrics, keys are alphabetical and the compact view shows at most six, with a warning directing the user to `--metrics` or `--verbose`.

Rows with missing or non-numeric sort values remain visible after ranked rows. JSON remains complete and lossless regardless of compact human column selection so the agent skill can consume it without scraping terminal formatting.

CSV/TSV source columns and string values are preserved. Sorted JSON rows receive an ordinal
`_rank`; rows with missing or non-numeric metrics have no rank.

The table backend requires each run producer to record a UTC ISO-8601 `created_at` timestamp. Hiraku injects launch context but does not invent or append the run row, so timestamp creation remains the responsibility of the tracking backend or training script.

### Open the experiment dashboard

Install the dashboard extra, then open a strictly read-only dashboard for an explicit or current
task:

```bash
uv sync --extra dashboard
hiraku dashboard heart-disease
```

The command starts a local server on `127.0.0.1` using an available port, opens the browser, and runs
until interrupted. `--no-open` prints the URL without opening a browser; `--host` and `--port` allow
explicit overrides. A manual refresh reloads task, experiment, Git, and tracking data without file
watching or polling.

The dashboard renders `task.md`, experiment and run tables, Git-derived lineage for branched
experiments, and an Altair/Vega-Lite metric timeline. Its performance frontier is the cumulative
minimum or maximum according to the selected task metric. Experiment and experiment-status filters
update the run table and chart and recompute the frontier from visible numeric runs. Run execution
status remains metadata rather than a separate filter.

All browser assets are pinned and bundled for offline use. The dashboard never edits records, run
data, Git state, or reviews. Table dashboards require `hiraku[dashboard]`; MLflow dashboards require
both the `dashboard` and `mlflow` extras.
