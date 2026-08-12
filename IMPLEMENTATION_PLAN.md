# Hiraku PoC Implementation Plan — impl1

## Summary

Implement a Python 3.14 proof of concept supporting a guided end-to-end workflow:

`init → create experiment → define named entrypoints → launch → inspect/rank runs`

The implementation branch is based on the clean `feat/poc` branch at `e6ea66c`. This plan is committed separately before implementation code is introduced.

Two guided examples will be provided:

- A tiny deterministic smoke example for fast diagnosis.
- A realistic binary-classification experiment using the 303-row Cleveland cohort from the [UCI Heart Disease dataset](https://archive.ics.uci.edu/dataset/45/heart+disease).

## Core Implementation

### Package and initialization

- Create a `src`-layout package managed by `uv`, targeting Python `>=3.14,<3.15`.
- Use Typer, PyYAML, and Rich. Use pytest and Ruff for development.
- Provide:
  - `hiraku init`
  - `hiraku exp create|list|show`
  - `hiraku experiment create|list|show`
  - `hiraku launch <experiment>:<entrypoint>`
  - `hiraku runs <experiment>`
- `hiraku init` must run at the Git root and fail before writing if `.hiraku.yml` exists or any generated target would be overwritten.
- Generate `.hiraku.yml`, the experiment body template, guided/auto policy defaults, and `.agents/skills/hiraku/`.
- Other commands locate the project by searching upward for `.hiraku.yml`.
- Resolve configured paths inside the project root and reject absolute paths or traversal outside it.

Default configuration:

```yaml
experiments_dir: experiments
experiment_template: experiments/template.md
policies:
  guided: .hiraku/policies/guided.md
  auto: .hiraku/policies/auto.md
tracking:
  backend: table
  path: .hiraku/runs.csv
```

### Experiment lifecycle

- Accept IDs matching `^[a-z0-9]+(?:-[a-z0-9]+)*$`.
- `exp create` defaults to `guided` mode and branch `exp/<id>`, with `--mode guided|auto` and a Git-validated `--branch` override.
- Require a clean Git worktree, create and switch to the experiment branch, then generate:
  - `experiments/<id>/experiment.md`
  - `experiments/<id>/policy.md`
  - `experiments/<id>/launch.yml`
- Hiraku prepends authoritative `id`, `branch`, and `mode` front matter to the literal configured Markdown body template.
- Copy `.hiraku/policies/<mode>.md` to the experiment. The copy becomes authoritative and does not inherit later default-policy edits.
- Initialize `launch.yml` with an empty `entrypoints` map.
- Prevalidate paths and Git state. On a later failure, restore the original branch and remove only artifacts created by the failed operation.
- Human `exp show` prints the complete record. JSON output contains metadata only: `list --json` returns an array and `show --json` one object.
- Reject malformed records and disagreement between directory ID and front-matter ID.

### Named launching

Use per-experiment manifests:

```yaml
entrypoints:
  train:
    command: ["uv", "run", "python", "train.py", "--seed", "1"]
    cwd: "."
    env:
      EXAMPLE_SETTING: "value"
```

- Execute argv arrays directly without a shell.
- Commands are fixed; runtime argument forwarding is out of scope.
- Permit an optional project-relative `cwd` and string-valued environment map.
- Reject cwd escapes and environment keys beginning with `HIRAKU_`.
- Require a completely clean worktree, the experiment’s recorded branch, and a resolvable HEAD commit.
- Inject:
  - `HIRAKU_EXPERIMENT_ID`
  - `HIRAKU_GIT_COMMIT`
  - `HIRAKU_TRACKING_BACKEND=table`
  - absolute `HIRAKU_TABLE_PATH`
- Inherit the parent environment, apply manifest values, and finally inject reserved Hiraku values.
- Propagate the child exit status. Do not require or verify that a successful process wrote a run row.

### Table tracking and leaderboard

- Support one project-level append-only CSV or TSV table, inferred from the configured suffix.
- Require `experiment_id` and `commit`; preserve arbitrary additional columns.
- `hiraku runs <id>` filters exact experiment IDs and:
  - succeeds with no rows if the table does not exist;
  - rejects malformed input or missing required columns;
  - preserves file order when unsorted;
  - requires exactly one of `--ascending` or `--descending` with `--sort`;
  - sorts finite numeric values stably;
  - places missing/non-numeric values last;
  - assigns ordinal ranks `1..N`, including tied values;
  - emits a Rich table or a bare JSON row array.
- Preserve source strings and names in JSON. Add integer `_rank` only to sorted rows and reject a source `_rank` collision.

## Agent Skill and Policies

- Install a functional repository-local Hiraku skill routing between:
  - experiment creation/refinement;
  - guided implementation;
  - launching and analyzing runs;
  - bounded auto-mode loops.
- Guided policy requires explicit approval before implementation, commits, launches, findings, and conclusions.
- Auto policy records scope, evaluation protocol, permitted commands, resource limits, and stopping conditions.
- Policy approval is conversational rather than persisted. The skill must obtain explicit approval in the active interaction before beginning an auto loop.
- Auto behavior must pause before changing policy or scope and may not merge protected branches.
- CLI code does not claim to enforce these agent policies as a security boundary.

## Examples and Test Cases

### Deterministic smoke example

- Retain a dependency-light example with two fixed entrypoints that append predictable metrics.
- Provide a short guided walkthrough.
- Exercise its complete lifecycle in a temporary Git repository as a fast E2E test.

### UCI Heart Disease example

- Vendor the official processed Cleveland snapshot used by the demo.
- Record:
  - original filename and source URL;
  - SHA-256 checksum;
  - dataset creators and DOI;
  - CC BY 4.0 attribution and license link;
  - that the example is educational and not a diagnostic or clinical system.
- Verify the checksum, 303-row count, 14 selected variables, and expected target domain before modeling.
- Define the target as absence (`num == 0`) versus presence (`num > 0`), matching the conventional binary task described by UCI.
- Parse `?` as missing.
- Treat `age`, `trestbps`, `chol`, `thalach`, and `oldpeak` as numeric; treat the remaining predictors as categorical.
- Keep preprocessing inside the model pipeline:
  - numeric median imputation and scaling;
  - categorical most-frequent imputation and one-hot encoding with unknown-category handling.
- Compare two fixed entrypoints:
  - logistic regression;
  - random forest with fixed seed, fixed estimator count, bounded single-process execution, and no hyperparameter search.
- Use `StratifiedKFold(n_splits=5, shuffle=True, random_state=42)` for both models.
- Record one table row per model containing:
  - experiment ID and Git commit;
  - run ID and UTC timestamp;
  - model name;
  - dataset checksum;
  - CV seed and fold count;
  - mean and standard deviation of ROC AUC;
  - mean and standard deviation of accuracy.
- Rank the walkthrough leaderboard by `roc_auc_mean --descending`. Metric improvement is not an acceptance condition.
- Keep pandas and scikit-learn out of the core CLI dependencies and expose them through a `heart-demo` optional dependency extra.
- Automate the complete Heart Disease lifecycle in a temporary Git repository: initialize, commit, create the experiment branch, install/copy the fixed manifests, launch both models, and verify two ranked rows.

### Automated quality gate

- Unit-test configuration discovery, containment, YAML/front-matter parsing, ID/ref validation, numeric ranking, and CSV/TSV behavior.
- Integration-test initialization collisions, policy copying, custom branches, malformed records, dirty-tree rejection, and transactional rollback.
- Test launcher cleanliness/branch checks, manifest validation, cwd/environment handling, injected variables, and exit propagation.
- Test run filtering, missing tables, required columns, sorting, ordinal ties, non-numeric values, JSON contracts, and `_rank` conflicts.
- Test skill assets and guided/auto routing instructions.
- Add GitHub Actions on Python 3.14 running Ruff, pytest with the `heart-demo` extra, and a package build/install smoke test.

## Documentation and Deferred Work

- Update README examples to match named entrypoints and table tracking.
- Correct the public environment prefix from `HARIKU_*` to `HIRAKU_*`.
- Document both guided examples and distinguish implemented behavior from the broader PoC roadmap.
- Defer MLflow, additional tracking adapters, direct arbitrary-command launching, project-wide leaderboards, dashboards, schedulers, HPO orchestration, and pull-request integration.
- Both examples are guided. They do not satisfy the broader requirement for two demonstrations in both guided and auto modes.
- After committing `IMPLEMENTATION_PLAN.md`, pause for user review before implementation.
