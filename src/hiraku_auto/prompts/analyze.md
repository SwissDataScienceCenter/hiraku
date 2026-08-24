{{rules}}

---

# Task: analyse a node's runs and write its findings

You are given one experiment, its runs from the run table, and its goal.

- Read metrics ONLY from the run rows. Never restate a number that is not there.
- Compare against the goal's `primary_metric` in its `direction`, and against the goal's
  `gate_metric` if it has one. A result that improves the primary metric while degrading the gate is
  not an improvement under this goal.
- Report seed-to-seed spread when more than one seed is present. If only one seed ran, say that the
  result is a candidate rather than a result.
- Mark any `reproducible: false` run as a debugging observation, not evidence.
- If the expectation is met, `verdict` is `supports`. You may set `promotable_candidate` to flag a
  run worth putting through the promotion gate, but you may NOT decide that it is promoted — that is
  an acceptance judgement and it is not yours.

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
  "promotable_candidate": "run_id worth putting through the promotion gate, or null",
  "missing_evidence": "what would need to be run to settle this, or null"
}
```
