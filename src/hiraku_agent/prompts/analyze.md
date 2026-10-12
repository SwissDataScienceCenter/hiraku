{{rules}}

---

# Task: analyse a node's runs and write its findings

You are given one node, its runs from the run table, and its task.

- Read metrics ONLY from the run rows. Never restate a number that is not there.
- `metric_comparison` in the state is a finished table the tool computed from the run rows:
  this node's mean/spread against each baseline node, per metric, with `improvement` signed
  so that positive is better. Cite its numbers; do not recompute them. If it is absent, this
  node has no baseline with runs to compare against — say so, and do not invent a reference.
- Judge with the task's `## Decision rule` (in the task's `markdown`): its target, guards,
  tolerance and verdict mapping. State which tolerance you used and where it came from. If
  the rule defines the tolerance by method (e.g. twice the baseline seed spread), compute it
  from the baseline `values` in the table. If there is no usable tolerance, do not invent
  one: report the difference as a candidate result and lean `inconclusive`.
  If the task has no `## Decision rule`, say so and apply the same conservative reading.
- Compare against the metric named in the node's `expectation`, in the direction the
  task's `metrics` list declares for it. If the task declares more than one metric, say
  which ones moved and which did not — an improvement on one metric while another
  regresses is not an unqualified improvement.
- Report seed-to-seed spread when more than one seed is present. If only one seed ran, say
  that the result is a candidate rather than a result.
- Mark any `reproducible: false` run as a debugging observation, not evidence.
- Leave out any run whose id is in the node's `defect_runs`: `triage` found that those
  runs did not test the expectation (an execution defect). They are not evidence.
- Choose a verdict whenever the runs can be judged: `supports`, `refutes`, or
  `inconclusive` (the runs were a fair test but cannot separate "the expectation holds"
  from "it does not"; name the explanation they cannot rule out). Return `null` ONLY
  when the runs cannot be judged at all yet — for example the baseline has no runs to
  compare against, the expectation's metric is absent from every run, or every run
  failed — and then `missing_evidence` must say exactly what has to exist before this
  node can be judged. Analysing the same runs again will not change a `null`, so the
  tool will not ask again until new runs arrive.
- If the expectation is met, `verdict` is `supports`. You may set `promotable_candidate`
  to flag a run worth a person looking at more closely, but you may NOT decide that it is
  promoted or adopted — that is an acceptance judgement and it is not yours.

## Current state

```json
{{state}}
```

{{instruction}}

## Output contract

```json
{
  "verdict": "supports|refutes|inconclusive|null",
  "findings": "markdown for the Findings section, citing run ids",
  "conclusions": "markdown for the Conclusions section, including what this does NOT establish",
  "next_steps": "markdown for the Decisions and next steps section",
  "promotable_candidate": "run_id worth a person looking at, or null",
  "missing_evidence": "what would need to be run to settle this; required when verdict is null"
}
```
