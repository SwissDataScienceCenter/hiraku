# CLI walkthrough and contract

Install the project and optional UI/demo dependencies:

```bash
uv sync --extra dev --extra tui --extra heart-demo
```

Initialize and commit the repository-local configuration before creating a Goal:

```bash
hiraku init
git add .hiraku.yml .hiraku/.gitignore .agents
git commit -m "initialize hiraku"
```

Create a Goal. Its launch manifest is validated and copied into SQLite, so Experiment branches do
not need a `launch.yml` commit:

```bash
hiraku goal create heart-disease \
  --metric roc_auc_mean \
  --direction max \
  --launch examples/heart-disease/launch.yml
hiraku goal show heart-disease --json
```

Create a baseline or hypothesis Experiment. Creation checks out `hiraku/<goal>/<experiment>`:

```bash
hiraku experiment create heart-disease/logistic \
  --kind baseline \
  --hypothesis "Logistic regression establishes the reference."
hiraku experiment create heart-disease/random-forest \
  --hypothesis "Random forest improves ROC AUC."
```

Root Experiments always start at the Goal baseline, so sibling creation does not require a manual
branch switch. A child starts at its parent's frozen commit:

```bash
hiraku experiment create heart-disease/class-weighted-rf \
  --parent heart-disease/random-forest \
  --hypothesis "Class weighting improves minority-class performance."
```

Launch a named entrypoint:

```bash
hiraku launch heart-disease/logistic:evaluate
hiraku launch heart-disease/logistic:evaluate --commit HEAD
```

The first launch freezes the Experiment commit. The normal form requires the active branch to be
clean and correct. `--commit` validates the ref is on the Experiment branch, runs it in a temporary
detached worktree, and leaves the active checkout unchanged.

Every validated launch creates a Run before starting the child. Project code can write a JSON object
to `$HIRAKU_METRICS_PATH`:

```json
{"parameters": {"seed": 42}, "metrics": {"roc_auc_mean": 0.84}}
```

or use the active Run directly:

```bash
hiraku run log --param seed=42 --metric roc_auc_mean=0.84
```

Parameters are immutable once set; active metrics may be updated. Missing metrics, startup errors,
non-zero exits, and signals still leave a durable Run.

Inspect and export evidence:

```bash
hiraku runs heart-disease --sort roc_auc_mean --descending
hiraku runs heart-disease/random-forest --json
hiraku report generate heart-disease
hiraku experiment tree heart-disease
hiraku best heart-disease
hiraku tui
```

Conclude an evaluated Experiment without changing source selection:

```bash
hiraku experiment conclude heart-disease/random-forest --verdict supports
hiraku experiment abandon heart-disease/failed-attempt
```

## Auto commands

```bash
hiraku auto context heart-disease
hiraku auto baseline heart-disease
hiraku auto proposal create heart-disease/random-forest \
  --hypothesis "Random forest improves ROC AUC."
hiraku auto proposal discover heart-disease
hiraku auto proposal list heart-disease
hiraku auto next heart-disease
hiraku auto implement heart-disease/random-forest --summary "Implemented the proposed model."
hiraku auto analyze heart-disease/random-forest
hiraku auto loop heart-disease --max-experiments 5 --max-runs 20 --time-budget 2h
```

The portable agent workflow uses `context`, `baseline`, `proposal create`, `next`, `implement`, the
normal launcher, and `analyze`. An already-running coding agent follows these commands directly
through the repository-local skill.

`auto loop --adapter <executable>` provides unattended execution through the runtime-neutral JSON
protocol. Without `--adapter`, `auto loop` retains the deterministic manifest implementation path
for cheap integration demos. That fallback is workflow automation, not hypothesis-generating AI.
