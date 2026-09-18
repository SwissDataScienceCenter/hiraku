{{rules}}

---

# Task: analyse a node's runs and write its findings

You are given one node, its runs from the run table, and its task.

- Read metrics ONLY from the run rows. Never restate a number that is not there.
- Compare against the metric named in the node's `expectation`, in the direction the
  task's `metrics` list declares for it. If the task declares more than one metric, say
  which ones moved and which did not — an improvement on one metric while another
  regresses is not an unqualified improvement.
- Report seed-to-seed spread when more than one seed is present. If only one seed ran, say
  that the result is a candidate rather than a result.
- Mark any `reproducible: false` run as a debugging observation, not evidence.
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
  "missing_evidence": "what would need to be run to settle this, or null"
}
```
