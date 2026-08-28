# Matteo: Bounded autonomous research over one lifecycle core

The main goal of the workflow is to give guided and autonomous research the same reliable foundation. A stable Goal defines the scope and success criterion; Experiments represent testable attempts; Runs capture durable execution evidence; and Analysis connects results back to the Goal. Hiraku owns this lifecycle in a local SQLite database, while Git records exact source provenance. Human-facing and agent-facing tools are different interfaces over the same authoritative state.

## Workflow

```bash
# 1. Define a measurable Goal, its constraints, and permitted entrypoints.
hiraku goal create heart-disease \
  --metric roc_auc_mean \
  --direction max \
  --launch examples/heart-disease/launch.yml
hiraku goal show heart-disease --json

# 2. Establish the baseline Experiment. Creation also creates and checks out
# its Git branch; the first launch freezes the exact evaluated commit.
hiraku experiment create heart-disease/logistic \
  --kind baseline \
  --hypothesis "Logistic regression establishes the reference model."
hiraku launch heart-disease/logistic:evaluate

# 3. Create and run a testable implementation attempt under the same Goal.
# Hiraku registers the Run before execution and finalizes it on success,
# failure, or interruption, permanently attaching commit and environment data.
hiraku experiment create heart-disease/random-forest \
  --hypothesis "Random forest improves ROC AUC."
hiraku launch heart-disease/random-forest:evaluate

# 4. Extend the lineage from a parent's frozen evaluated commit when needed.
hiraku experiment create heart-disease/class-weighted-rf \
  --parent heart-disease/random-forest \
  --hypothesis "Class weighting improves minority-class performance."
hiraku launch heart-disease/class-weighted-rf:evaluate

# 5. Analyze the evidence, inspect lineage, and record the conclusion.
hiraku runs heart-disease --sort roc_auc_mean --descending
hiraku experiment tree heart-disease
hiraku experiment conclude heart-disease/random-forest --verdict supports
hiraku report generate heart-disease
hiraku tui

# 6. Run the same lifecycle autonomously within an approved policy.
# The agent may propose, implement, launch, and analyze, but Hiraku remains
# responsible for state transitions, provenance, and execution records.
hiraku auto context heart-disease
hiraku auto proposal discover heart-disease
hiraku auto proposal list heart-disease
hiraku auto loop heart-disease \
  --adapter ./agent-adapter \
  --max-experiments 5 \
  --max-runs 20 \
  --time-budget 2h
```

## Preferred user interfaces

The root command-line interface and repository-local skill provide the guided workflow, with explicit approval and review of source changes and evidence. `hiraku auto` and a generic JSON adapter expose the same lifecycle to bounded autonomous agents. The TUI and generated reports are review surfaces over the shared query model; SQLite remains the single source of truth rather than an editable user interface.
