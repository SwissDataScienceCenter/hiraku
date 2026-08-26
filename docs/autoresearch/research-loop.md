# Research loop

Hiraku separates research intent, source implementation, and execution evidence:

```text
Goal → hypothesis Experiment → frozen commit → Run(s) → analysis → verdict
```

## Guided mode

The root CLI is deterministic. The installed repository-local skill asks for explicit approval before
creating an Experiment, editing or committing source, launching, concluding, or abandoning. The
skill calls CLI operations; it does not rewrite SQLite or generated reports.

The human reviews the Goal protocol and baseline first, then reviews each Experiment's hypothesis,
source diff, Run evidence, and conclusion.

## Auto mode

Before `hiraku auto loop`, the human approves the Goal, allowed entrypoints, implementation command,
experiment/run limits, time budget, and policy. The loop is serial in v0.1 and uses the same
ExperimentService, Launcher, RunService, and lineage queries as guided mode.

It stops before changing Goal scope, metric, direction, protocol, constraints, manifest permissions,
budgets, or policy. Fixed implementation, launch, and research-result failures are recorded as
events. A missing or non-numeric metric yields `inconclusive`; a numeric result is compared to the
best recorded baseline value and yields `supports` or `refutes`.

## Why parent links beat Git ancestry

Git answers “what source was this built from?” Research lineage answers “which question did this
attempt respond to?” A diagnostic can add instrumentation on the same source commit; a refinement
can be rebuilt from a baseline; rebases and cherry-picks rewrite Git ancestry without changing the
research question. Declared `parent_id` survives those operations and the Run still records the
exact commit for reproducibility.

## Why there is no promotion gate in v0.1

An Experiment conclusion records the evidence-backed interpretation. This PoC does not make an
accepted/current-best pointer, merge branches, or decide statistical significance. A later release
can add a promotion policy without changing the Goal/Experiment/Run lineage model.

