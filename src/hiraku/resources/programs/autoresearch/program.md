---
id: autoresearch
summary: Bounded evidence-driven experimentation over a defined Hiraku research task.
---

# Autoresearch

Run a bounded experiment loop for a selected Hiraku task.

The task defines the research contract. Work on one experiment at a time and use Hiraku as the authoritative source of research state, implementation provenance, runs, and conclusions.

## Preconditions

Before starting, verify that the selected task defines enough of the research contract to evaluate experiments consistently:

- a research objective or question,
- declared metrics and their directions,
- an evaluation protocol.

If these are materially incomplete, stop and return to guided research design rather than inventing them during the loop. A baseline is not a precondition when the task needs one: establish the baseline as the first experiment before testing improvements.

## Session budget

The experiment budget belongs to this Program, not to Hiraku's task metadata.
At the start of each session, identify the active limits from the user's request
or harness configuration. When no experiment count is supplied, use this default:

- Maximum experiments: 5, counting the baseline.

Do not impose a CPU time limit unless the user or harness explicitly specifies
one. A launcher may support an explicitly requested CPU limit, but the Program
does not set one by default.

Count every attempted experiment, including failed, invalid, and discarded
attempts. Do not launch another experiment after the experiment budget is
exhausted. A task's `experiment_budget` field is informational and does not
override the Program or explicit session limits.

Record the primary metric, evaluation protocol, and implementation files in
scope before the first proposal. If a limit or scope is missing and cannot be
reasonably inferred from the request, ask the harness or user before execution.

## Literature review handoff

If the user requested a literature review as part of this research workflow,
complete it before experiment design when no suitable task-linked literature
Context exists. Read and follow the `literature-review` Program, save the review
as a task-linked Context, deliver its synthesis, and then resume this
autoresearch workflow in the same session. The review is an initial phase, not
the stopping point. Do not create experiment records or implement code as part
of the review phase; begin the baseline and experiment loop after handing off
from it.

## 1. Inspect

Reconstruct the current research state from Hiraku rather than relying on conversation memory.

Inspect only what is needed to choose the next experiment:

- the task objective, metrics, protocol, and limits,
- existing experiments and their conclusions,
- relevant attributed runs,
- source code needed to understand the next change,
- current Git state.

Read task-linked Contexts supplied with the Program prompt, including any
persisted literature review, before choosing an experiment. Treat source
verification status and review limitations as part of the evidence quality.

Treat unsuccessful, abandoned, and inconclusive experiments as research memory. Do not repeat an idea merely because it failed.

Research relationships and execution configuration are separate. Do not assume that related experiments share an entrypoint or implementation structure.

## 2. Design or discover

Choose one focused next experiment grounded in the current evidence.

If the task needs a baseline and no valid baseline exists yet, make the
baseline the next experiment. Use the task's stated reference method when
available; otherwise choose the simplest meaningful comparison justified by
the research question and protocol. Implement and evaluate it before proposing
architectural improvements. Do not let the absence of baseline results block
the baseline itself. Once a valid baseline exists, choose follow-up experiments
that test whether a justified change improves on it.

The experiment must state:

- one falsifiable hypothesis,
- the mechanism or explanation being tested,
- the smallest intended change,
- the expected effect on a declared task metric,
- what observation would support or refute the hypothesis,
- the evaluation needed to distinguish the relevant explanations,
- the experiment-local execution required to test it.

For a baseline, the hypothesis may establish an expected reference result and
the measurement needed to make later comparisons meaningful; it need not claim
that the baseline improves on a prior method.

Before proposing it, verify that the experiment is:

- **causal enough** — it names a specific mechanism or explanation,
- **discriminating** — possible outcomes distinguish meaningful alternatives,
- **novel** — existing research history has not already tested it,
- **in scope** — it preserves the task's research question and evaluation protocol,
- **proportionate** — its expected information is worth its cost,
- **measurable** — it can be judged using a declared task metric.

If no candidate satisfies these conditions, stop. Exhausting justified experiments is a valid research outcome.

Create only the selected experiment. Do not materialize a speculative tree of future experiments.

## 3. Implement

Before editing source code, verify that the selected change can be isolated
safely from existing user work.

Inspect the status and staged paths. Preserve existing work, but do not treat
unrelated untracked files (including Hiraku research records) as an
implementation conflict. Hiraku records are research state, not implementation
files; do not stage them with experiment code. Stop only when existing changes
overlap the files the experiment must change and cannot be preserved safely,
or when unrelated staged paths prevent a scoped commit.

Implement only the selected experiment, within the user's request and the
harness's available execution permissions. This Program defines the research
workflow; it does not grant operating-system, repository, or harness
permissions.

First determine whether the experiment requires a code change. A baseline may
evaluate an implementation that is already present in the project. In that
case, do not try to commit unchanged files or create a no-op commit. Verify
which commit contains the implementation, then bind that commit to the record
through Hiraku (for example, `hiraku record update <id> --commit <sha>`).
Only use `commit_implementation` when the experiment actually changed
implementation files that need a new commit.

Define the experiment's runnable entrypoint from its actual implementation
rather than inheriting execution configuration from research ancestry.

Bind the resulting implementation to the experiment through Hiraku, committing
only the selected implementation paths. A dirty worktree by itself does not
prevent a scoped commit. If a Hiraku operation fails, inspect the full error
and current record/Git state.
Choose the appropriate supported operation for the state; do not retry a
commit when there are no changes to commit, or bypass a validation constraint.

## 4. Launch

Run the implementation through Hiraku using the experiment's declared
execution configuration. Apply a CPU time limit only when the user or harness
explicitly specified one. An implementation already committed before the
proposal can still be launched once that commit is bound to the experiment
record.

The MCP `launch_experiment` tool returns a launch ID without waiting for the
process to finish. Poll `get_launch_status` until it reports `completed`,
`failed`, or `interrupted`. Do not analyze a running launch or start another
experiment while it is unresolved. For a failed or interrupted launch, inspect
the returned exit code and log tail, preserve the attempted experiment, and
decide whether the same hypothesis can be validly rerun within the remaining
budget. A new launch is a new attempt and counts against the budget.

Require explicit experiment attribution.

A successful process without an attributed run is not experimental evidence.

Preserve failed, crashed, and invalid runs as part of the research history when they are attributable. Do not silently retry until a favorable result appears.

## 5. Analyze

Inspect the experiment's attributed runs and judge the evidence against the hypothesis recorded before execution.

Compare the result with:

- the stated hypothesis,
- the task's declared metrics and directions,
- the baseline or relevant prior evidence,
- required correctness or validity checks.

Use only evidence attributable to the experiment and its implementation state.

Classify the result as:

- **supported** — the observed evidence supports the stated hypothesis,
- **refuted** — valid evidence contradicts the stated hypothesis,
- **inconclusive** — the available evidence cannot distinguish the relevant explanations.

Do not equate an improved single metric with support when other declared metrics or validity checks materially conflict.

Cite the actual run IDs used and record the evidence-backed conclusion through Hiraku.

## 6. Triage

After analysis, decide whether the current experiment justifies another experiment.

Analyze asks what the current experiment established.

Triage asks whether the current result raises a worthwhile unanswered question.

A supported hypothesis does not automatically justify continuation. A refuted hypothesis does not automatically end the research.

Before creating a follow-up to the current experiment, require a concrete next question that satisfies the design criteria:

- named mechanism,
- discriminating test,
- novelty,
- task scope,
- proportionate cost,
- declared metric.

Execution defects do not justify a new research hypothesis. Correct the same experiment when the existing run failed to test its hypothesis validly.

If the evidence is inconclusive, name the competing explanation that remains unresolved before proposing a follow-up.

If a justified follow-up exists, continue with that experiment.

If no justified follow-up exists, mark the current branch as exhausted and reassess the task as a whole.

## Continue or stop

After each analyzed experiment, reconstruct current state from Hiraku before deciding what to do next.

First evaluate whether the current branch has a justified follow-up.

If the branch is exhausted, return to Design or Discover and ask whether another independent experiment is justified by:

- the task objective,
- unresolved questions,
- existing research history,
- literature or other recorded evidence,
- the remaining experiment and resource budget.

Independent experiments should normally be siblings under the task rather than children of an unrelated experiment.

Continue only when:

- a justified follow-up or independent task-level experiment exists,
- the harness permits the required operation,
- the task's research contract remains unchanged,
- all configured limits remain satisfied.

Do not continue merely because budget remains.

Stop when:

- the Program's experiment budget or another session resource limit is exhausted,
- the research objective has been answered sufficiently,
- neither the current branch nor the task as a whole has another experiment that satisfies the design criteria,
- further progress requires changing the task's research question, metrics, or evaluation protocol,
- further progress requires a new user decision,
- a Hiraku or project constraint blocks safe continuation.

Branch exhaustion does not imply task exhaustion.

Stopping does not imply success. Record the strongest conclusion supported by the available evidence and any important remaining uncertainty.

Do not bypass a Hiraku validation constraint or harness permission boundary to
keep the loop running.
