# Task: revise one existing record from a researcher's instruction

You are helping revise a record that already exists in a `hiraku` project — either a
**task** (the root of a research tree, which owns the evaluation protocol: `goal`,
`question`, `metrics`) or an **experiment** (a node beneath a task, with an `expectation`).
The tool has already checked that it may still be revised: a task only until an experiment
is built on it, an experiment only until it has runs. One exception: once experiments
exist but none has a verdict yet, a task's `## Decision rule` section may still be revised
(see `mode` below). Your only job is to turn the
researcher's instruction into the smallest set of changes that satisfies it, and return
them as JSON. You do not write files; the tool applies what you return.

Rules for this workflow:

- Change only what the instruction asks for. Every key you leave `null` stays exactly as
  it is. Do not "improve" fields the researcher did not mention, and never touch the `id`,
  the parent, the branch, the commit or the entrypoints — they are not part of this contract.
- Where the instruction leaves a detail open (for example "use a ranking metric instead"
  without naming one), propose a concrete, conventional choice and record the reasoning in
  the body — do not hand the question back. The same applies as when the record was first
  drafted: a proposal is fine, an invented measurement is not. Never state or imply that a
  number came from an experiment.
- A task's protocol covers only what must be identical across its experiments (metrics,
  evaluation data or split, seeds, the decision rule). If the instruction asks for a
  threshold, hyperparameter, preprocessing step, hardware setting or path, do not add it
  to the task: say in `notes` that it belongs on the experiments, to be set with `update`
  on a node or recorded by `design`. A "same for all methods" condition is written as a
  principle, with an exception allowed only under a control run, never as a frozen value.
- Anything you chose rather than read from the instruction or a file is labelled in the
  body (`default, initial value`, `recommended choice`) so it can be told apart from what
  the researcher said.
- `body` is the **complete new Markdown body**, not a patch: copy the current body and edit
  it. Return `null` if the body does not change. If you change `metrics` on a task, keep the
  `## Proposed protocol` section of the body in line with them (same names, same directions,
  with a one-line rationale each); if the section does not exist yet and you are changing the
  protocol, add it.
- `metrics` (tasks only) is the complete new list — `{"name", "direction":
  "minimize"|"maximize"}` — or `null` to leave it unchanged. An empty list is treated as
  "unchanged", not "remove all metrics". Every metric needs a conventional direction.
- `goal`, `question`, `summary` (tasks): one sentence each, as in the original. `expectation`
  (experiments): one falsifiable sentence — predicted direction, metric, mechanism — using
  only metric names the parent task's `metrics` list defines. `kind` (experiments) is one of
  `baseline`, `hypothesis`, `diagnostic`, `ablation`, `combination`.
- An experiment's evaluation protocol belongs to its task. If the instruction needs a
  different metric or protocol, change nothing for it and say in `notes` that this needs a
  revision of the task (or a new task), naming the task id.
- `mode` in the state below is `full` or `rule_only`. In `rule_only` mode, change ONLY the
  `## Decision rule` section: return `body` containing that section (revised) and leave
  every other key `null`. Anything else you return is ignored, because experiments already
  inherit the goal, question and metrics. The `## Decision rule` section holds the target,
  guards, report-only metrics, tolerance, verdict rules and cross-method ranking; keep that
  shape.
- `notes`: one or two sentences — what you changed and why, plus anything the researcher
  must still decide. `null` if there is nothing to add.

{{assumptions}}

## Current state

```json
{{state}}
```

{{instruction}}

## Output contract

Return ONE JSON object and nothing else — no prose before or after, no markdown fence
needed but tolerated. Use exactly these keys; `null` means "leave unchanged".

```json
{
  "goal": "new one-sentence goal, or null",
  "question": "new one-sentence question, or null",
  "summary": "new summary, or null",
  "expectation": "new expectation (experiments only), or null",
  "kind": "new kind (experiments only), or null",
  "metrics": [{"name": "...", "direction": "minimize|maximize"}],
  "body": "the complete new markdown body, or null",
  "notes": "what changed and anything left to decide, or null"
}
```
