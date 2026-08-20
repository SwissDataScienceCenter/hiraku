# Hiraku PoC Implementation Plan

## Summary

Implement a Python 3.14 proof of concept supporting an end-to-end workflow:

`init → create experiment → define named entrypoints → launch → inspect/rank runs`

Three standalone examples will be provided:

- A tiny deterministic smoke example for fast diagnosis.
- A realistic binary-classification experiment using the 303-row Cleveland cohort from the [UCI Heart Disease dataset](https://archive.ics.uci.edu/dataset/45/heart+disease).
- An autoresearch example following [Karpathy's autoresearch](https://github.com/karpathy/autoresearch); its detailed design will be completed after reviewing that project.

The intermediate lineage level between experiments and runs remains deliberately unresolved. It
will be added to this plan after its data model and naming are agreed; unaffected PoC requirements
remain unchanged.

## Core Implementation

### Package and initialization

- Create a `src`-layout package managed by `uv`, targeting Python `>=3.14,<3.15`.
- Use Typer, PyYAML, and Rich. Use pytest and Ruff for development.
- Provide:
  - `hiraku init`
  - `hiraku exp create|list|show`
  - `hiraku experiment create|list|show`
  - `hiraku launch [--commit <ref>] <experiment>:<entrypoint>`
  - `hiraku runs <experiment>`
- `hiraku init` must run at the Git root and fail before writing if `.hiraku.yml` exists or any generated target would be overwritten.
- Generate `.hiraku.yml`, complete `guided` and `auto` experiment templates, and `.agents/skills/hiraku/`.
- Other commands locate the project by searching upward for `.hiraku.yml`.
- Resolve configured paths inside the project root and reject absolute paths or traversal outside it.

Default configuration:

```yaml
experiments_dir: experiments
templates_dir: .hiraku/templates
default_template: guided
tracking:
  backend: table
  path: .hiraku/runs.csv
```

### Experiment lifecycle

- Accept IDs matching `^[a-z0-9]+(?:-[a-z0-9]+)*$`.
- `exp create` defaults to template `guided` and branch `exp/<id>`, with `--template <name>` and a Git-validated `--branch` override.
- Do not require a clean worktree. Create and switch to the experiment branch, then initialize it from `.hiraku/templates/<name>/`, generating:
  - `experiments/<id>/experiment.md`
  - `experiments/<id>/program.md`
  - `experiments/<id>/policy.md`
  - `experiments/<id>/launch.yml`
- Each template is a complete bundle containing all four files. `guided` and `auto` are ordinary bundled templates, not runtime modes; projects may add more bundles such as `karpathy`.
- Prevalidate that the selected template exists and contains all four required files before creating a branch or writing anything.
- Hiraku prepends authoritative `id` and `branch` front matter to the copied `experiment.md`. Do not record the originating template and remove `mode` from the experiment model.
- The copied files become independently editable and authoritative; they do not inherit later template changes.
- `experiment.md` defines scientific scope, evaluation, findings, conclusions, and decisions. `program.md` defines the agent workflow or research algorithm. `policy.md` defines permissions, approval boundaries, resource limits, and stop/escalation conditions. `launch.yml` defines executable entrypoints.
- Policy always constrains program; a program cannot grant itself additional authority.
- Prevalidate paths and Git branch state, without requiring cleanliness. On a later failure, restore the original branch and remove only artifacts created by the failed operation.
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
- `hiraku launch <experiment>:<entrypoint>` runs directly from the current repository state, including tracked and untracked changes. It does not capture or hash diffs and marks the run non-reproducible.
- `hiraku launch --commit <ref> <experiment>:<entrypoint>` resolves the ref to a full SHA and runs the committed snapshot in a temporary detached Git worktree without modifying the active checkout.
- For commit-backed launches, read configuration, experiment artifacts, manifest, and code from the isolated worktree; resolve `cwd` there; send tracking output to the original repository's configured destination; and remove the temporary worktree afterward.
- Do not require the active worktree to be clean in either launch form. A configurable `launch.require_reproducible` setting is deferred.
- Inject source provenance alongside:
  - `HIRAKU_EXPERIMENT_ID`
  - `HIRAKU_TRACKING_BACKEND=table`
  - absolute `HIRAKU_TABLE_PATH`
- A working-tree launch is identified as non-reproducible and may record current `HEAD` only as its base commit. A commit-backed launch records the resolved SHA as its executed source commit and is identified as reproducible. Exact persistence fields will be finalized with the intermediate lineage model.
- Inherit the parent environment, apply manifest values, and finally inject reserved Hiraku values.
- Propagate the child exit status. Do not require or verify that a successful process wrote a run row.

### Table tracking and leaderboard

- Support one project-level append-only CSV or TSV table, inferred from the configured suffix.
- Preserve arbitrary table columns. The final required provenance columns will be settled with the intermediate lineage layer; they must distinguish non-reproducible working-tree runs from reproducible commit-backed runs.
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

## Agent Skill, Programs, and Policies

- Install a functional repository-local Hiraku skill routing between:
  - experiment creation/refinement;
  - executing the experiment's program within its policy;
  - launching and analyzing runs;
  - bounded autonomous loops.
- The `guided` template initializes a stepwise program and a policy requiring explicit approval before implementation, commits, launches, findings, and conclusions.
- The `auto` template initializes an autonomous-loop program and a bounded policy recording permitted commands, resource limits, stopping conditions, and approval boundaries.
- Policy approval is conversational rather than persisted. The skill must obtain explicit approval in the active interaction before beginning an auto loop.
- Programs must pause before exceeding or changing policy or scope and may not merge protected branches without explicit approval.
- CLI code does not claim to enforce these agent policies as a security boundary.

## Examples and Test Cases

### Deterministic smoke example

- Retain a dependency-light example with two fixed entrypoints that append predictable metrics.
- Organize it as a self-contained repository template with root-relative commands and paths.
- Document copying it to a new destination, running `git init`, and executing the lifecycle there; do not recommend a nested repository inside the Hiraku checkout.
- Exercise the documented copy-and-`git init` lifecycle in a temporary repository as a fast E2E test.

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
  - experiment ID and source provenance appropriate to the launch form;
  - run ID and UTC timestamp;
  - model name;
  - dataset checksum;
  - CV seed and fold count;
  - mean and standard deviation of ROC AUC;
  - mean and standard deviation of accuracy.
- Rank the walkthrough leaderboard by `roc_auc_mean --descending`. Metric improvement is not an acceptance condition.
- Keep pandas and scikit-learn out of the core CLI dependencies and declare the example's modeling dependencies in its self-contained project metadata.
- Automate the documented copy-and-`git init` lifecycle in a temporary repository: initialize, commit, create the experiment branch, install/copy the fixed manifests, launch both models, and verify two ranked rows.

### Autoresearch example

- Add a third self-contained example following Karpathy's autoresearch workflow.
- Initialize it with a dedicated `karpathy` experiment template demonstrating a distinct `program.md` and bounded `policy.md`.
- Finalize its algorithm, permitted mutations, evaluation loop, resource bounds, and acceptance tests only after reviewing the referenced project.

### Automated quality gate

- Unit-test configuration discovery, containment, YAML/front-matter parsing, ID/ref validation, numeric ranking, and CSV/TSV behavior.
- Integration-test initialization collisions, full-template copying, missing template files, custom templates, custom branches, malformed records, dirty-worktree creation, and transactional rollback.
- Test working-tree launches with tracked and untracked changes, commit-ref resolution, isolated worktree execution and cleanup, manifest/config loading from the target commit, tracking output persistence, cwd/environment handling, injected provenance, and exit propagation.
- Test run filtering, missing tables, required columns, sorting, ordinal ties, non-numeric values, JSON contracts, and `_rank` conflicts.
- Test skill assets, program/policy precedence, and guided/auto template instructions.
- Add GitHub Actions on Python 3.14 running Ruff, pytest with each example's dependencies, and a package build/install smoke test.

## Documentation and Deferred Work

- Update README examples to match named entrypoints and table tracking.
- Correct the public environment prefix from `HARIKU_*` to `HIRAKU_*`.
- Document all standalone examples and distinguish implemented behavior from the broader PoC roadmap.
- Defer MLflow, additional tracking adapters, direct arbitrary-command launching, project-wide leaderboards, dashboards, schedulers, HPO orchestration, and pull-request integration.
- Defer `launch.require_reproducible` and a CLI example-materialization command.
- Defer the intermediate experiment-to-run lineage model and its naming until the design discussion resumes.
