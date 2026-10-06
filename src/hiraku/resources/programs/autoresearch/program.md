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
- an evaluation protocol,
- a baseline or meaningful comparison,
- experiment, run, time, compute, or cost limits as applicable.

If these are materially incomplete, stop and return to guided research design rather than inventing them during the loop.

## 1. Inspect

Reconstruct the current research state from Hiraku rather than relying on conversation memory.

Inspect only what is needed to choose the next experiment:

- the task objective, metrics, protocol, and limits,
- existing experiments and their conclusions,
- relevant attributed runs,
- source code needed to understand the next change,
- current Git state.

Treat unsuccessful, abandoned, and inconclusive experiments as research memory. Do not repeat an idea merely because it failed.

Research relationships and execution configuration are separate. Do not assume that related experiments share an entrypoint or implementation structure.

## 2. Design or discover

Choose one focused next experiment grounded in the current evidence.

The experiment must state:

- one falsifiable hypothesis,
- the mechanism or explanation being tested,
- the smallest intended change,
- the expected effect on a declared task metric,
- what observation would support or refute the hypothesis,
- the evaluation needed to distinguish the relevant explanations,
- the experiment-local execution required to test it.

Before proposing it, verify that the experiment is:

- **causal enough** — it names a specific mechanism or explanation,
- **discriminating** — possible outcomes distinguish meaningful alternatives,
- **novel** — existing research history has not already tested it,
- **in scope** — it preserves the task's research question and evaluation protocol,
- **proportionate** — its expected information is worth its cost,
- **measurable** — it can be judged using a declared task metric.

If no candidate satisfies these conditions, stop. Exhausting justified experiments is a valid research outcome.

Create only the selected experiment. Do not materialize a speculative tree of future experiments.

## 3. Approve

Present the concrete experiment proposal before implementation.

Use the harness's interactive confirmation mechanism when available.

Only explicit approval authorizes implementation.

If interactive confirmation is unavailable, stop at this checkpoint and request approval.

Approval is permission to test the proposal. It is not evidence that the hypothesis is correct.

## 4. Implement

Before editing source code, verify that the approved change can be isolated safely from existing user work.

If pre-existing changes overlap the implementation and cannot be preserved safely, stop and ask the user how to proceed.

Implement only the approved experiment.

Define the experiment's runnable entrypoint from its actual implementation rather than inheriting execution configuration from research ancestry.

Bind the resulting implementation to the experiment through Hiraku.

If Hiraku rejects the operation, diagnose the reported constraint rather than bypassing it.

## 5. Launch

Run the approved implementation through Hiraku using the experiment's declared execution configuration.

Require explicit experiment attribution.

A successful process without an attributed run is not experimental evidence.

Preserve failed, crashed, and invalid runs as part of the research history when they are attributable. Do not silently retry until a favorable result appears.

## 6. Analyze

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

## 7. Triage

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
- the user's authorization covers further experimentation,
- the task's research contract remains unchanged,
- all configured limits remain satisfied.

Do not continue merely because budget remains.

Stop when:

- the experiment, run, time, compute, or cost budget is exhausted,
- the research objective has been answered sufficiently,
- neither the current branch nor the task as a whole has another experiment that satisfies the design criteria,
- further progress requires changing the task's research question, metrics, or evaluation protocol,
- further progress requires a new user decision,
- a Hiraku or project constraint blocks safe continuation.

Branch exhaustion does not imply task exhaustion.

Stopping does not imply success. Record the strongest conclusion supported by the available evidence and any important remaining uncertainty.

Do not bypass a Hiraku constraint merely to keep the loop running.