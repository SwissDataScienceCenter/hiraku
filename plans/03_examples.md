# Plan 03: Deterministic Heart Disease Example

## Objective

Implement the end-to-end example specified in `docs/examples.md`. The milestone
adds one copyable, offline Heart Disease project and one integration test that
executes the documented workflow in a fresh temporary Git repository.

The example is a conformance demonstration for the fixed data and CLI
contracts. It must use only deterministic core commands: initialize a project,
create one task, create two committed experiments with experiment-level
entrypoints, launch both models, and query their CSV runs. Do not add agent
invocation, templates, programs, dashboards, MLflow, or autonomous behavior.

Keep `docs/cli.md` and `docs/data.md` unchanged. If implementation exposes a
contradiction in those contracts, stop and resolve the contract rather than
adding example-specific behavior to Hiraku.

## Example project

Create the following self-contained project:

```text
examples/heart-disease/
├── data/
│   └── processed.cleveland.data
├── .gitignore
├── README.md
├── pyproject.toml
└── train.py
```

Bundle the processed Cleveland data file used by the earlier scratch example.
Verify before committing it that its SHA-256 digest is
`a74b7efa387bc9d108d7d0115d831fe9b414b29ae7124f331b622b4efa0427c8`.
The runtime must read this local file and must contain no download or network
fallback. Add concise UCI source and dataset attribution to the example README.

The example `pyproject.toml` requires Python 3.11 or newer and pins pandas and
scikit-learn to exact versions. Use the same versions in the repository's test
environment and lock file so model output is stable in CI. Keep NumPy
transitive unless the selected pandas/scikit-learn resolution allows multiple
NumPy versions that change the recorded metrics; in that case pin the resolved
NumPy version as well.

The README is the copyable tutorial for this project. Keep its commands
identical to `docs/examples.md`, including the task fields, metric declarations,
experiment hypotheses, entrypoint declarations, commit binding, launch targets,
and run-list queries. Explain that dependencies must already be installed
before the offline workflow starts.

Ignore only local environments, Python caches, and the generated task-local
`runs.csv`. Do not ignore the bundled dataset, `hiraku.yml`, initialized Hiraku
resources, or experiment records created while following the workflow.

## Training and run recording

Implement `train.py` as a small typed script with these command-line options:

```text
--model {logistic,random_forest}  required
--seed INTEGER                    default 42
--validation-fraction FLOAT       default 0.25
```

Load the bundled 14-column processed Cleveland file with `?` as missing data
and convert the `num` target to binary heart-disease presence. Use a stratified
train/validation split. Build one shared preprocessing pipeline:

- median imputation and standard scaling for numeric columns;
- most-frequent imputation and one-hot encoding for categorical columns;
- logistic regression with `max_iter=2000` and the supplied seed; or
- random forest with 300 estimators, the supplied seed, and `n_jobs=1`.

Record validation log loss from predicted probabilities and validation
accuracy from a `0.5` threshold. Reject an unknown model through argument
parsing, and let malformed data, missing provenance, training failures, and CSV
errors fail the process instead of producing a partial run.

Keep the CSV writer local to the example; it is not a new Hiraku logging API.
Require and read `HIRAKU_TASK_ID`, `HIRAKU_EXPERIMENT_ID`, `HIRAKU_COMMIT`,
`HIRAKU_ENTRYPOINT`, and `HIRAKU_TASK_FILE`. Resolve the output as
`Path(HIRAKU_TASK_FILE).with_name("runs.csv")` so commit-isolated launches write
back to the original project.

Write this exact header in a stable order:

```text
id,task_id,experiment_id,commit,timestamp,entrypoint,param.model,param.seed,param.validation_fraction,metric.val/log_loss,metric.val/accuracy
```

Use a UUID hex string for `id`, an aware UTC ISO-8601 timestamp for `timestamp`,
and fixed decimal formatting for both metrics. On an existing file, validate
that its header exactly matches before appending. Write the header only for a
new or empty file and append exactly one row after a successful model run.

## End-to-end integration test

Add one integration test module dedicated to the example. Copy
`examples/heart-disease/` into `tmp_path`; never initialize Git inside the
source example directory. Configure a local test Git identity, run `git init
-b main`, initialize Hiraku through the public CLI, add all initial files, and
create the shared source commit.

Drive the workflow through `click.testing.CliRunner` using the exact argument
values documented in `docs/examples.md`:

1. Create `heart-disease` with its goal, question, and two metric declarations.
2. Assert the task is active and has no task-level entrypoints.
3. Create `logistic-baseline` and `random-forest` with `--commit HEAD` and their
   respective experiment-level `train` entrypoints.
4. Assert both records contain the same 40-character commit hash and the
   expected commands and default arguments.
5. Launch `logistic-baseline:train` and `random-forest:train` without explicit
   commit overrides, exercising automatic experiment commit resolution and
   temporary-worktree execution.
6. Query all runs, one filtered experiment, explicit parameter/metric columns,
   and ascending `val/log_loss` ordering through the CLI.

Parse the resulting CSV independently and also load it through Hiraku's CSV
backend. Assert exactly two runs and verify for each:

- task `heart-disease`;
- experiment `logistic-baseline` or `random-forest`;
- entrypoint `train`;
- the shared full commit hash;
- the corresponding model name;
- seed `42` and validation fraction `0.25`;
- finite log-loss and accuracy values with accuracy in `[0, 1]`; and
- the expected metric values within tight tolerances determined from the
  pinned dependency environment.

Assert the sorted CLI result places the lower-log-loss model first and the
experiment filter returns only its requested run. Do not assert UUIDs,
timestamps, temporary-worktree paths, abbreviated human IDs, or spacing in
human tables. Use JSON output where exact structured assertions are needed.

The test must perform no network access. It may rely on dependencies installed
as part of the repository's development/test environment, but it must not run a
package installer inside the temporary example repository.

## Verification and acceptance

Run the focused example integration test first, then the complete test suite,
Ruff checks, and the existing package build smoke test. Confirm that:

- the example works when copied rather than run in place;
- both launches execute code from the bound shared commit;
- generated runs survive temporary-worktree cleanup;
- the source example directory remains unchanged after tests;
- `git diff --check` passes; and
- no production Hiraku API or CLI behavior changed for the example.

The milestone is complete when a clean checkout with the declared test
dependencies can execute the integration test offline and reproduce the two
normalized, correctly attributed model runs described in `docs/examples.md`.
