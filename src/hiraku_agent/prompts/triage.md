{{rules}}

---

# Task: triage a node whose result did not meet its expectation

You are given one node, its runs, its task, and the surrounding tree. This node's `state`
is `concluded_untriaged` or `awaiting_analysis` — either it already has a verdict with no
children, or it has runs and none yet; judge from what's in `focus_node` below rather than
assuming which.

Do this in order:

1. **Check preconditions.** If the node has no recorded `expectation`, stop: set
   `"blocked": "no expectation recorded"` and return. If every run is
   `reproducible: false`, say so — class A cannot be excluded.
2. **Classify** the failure into exactly one of A, B, C, with the evidence from the run
   rows.
3. **Conclude the node** — write `findings` and `conclusions` for THIS node, citing run
   IDs. A child must never be created while its parent is still open.
4. **Apply the investigation test** for any child you would propose. All six conditions,
   answered.
5. **Decide.** If any condition fails, `proceed` is false and `blocking_condition` names
   it.

For class A, `proceed` is false and `children` is empty: a defect is fixed on the same
node, and no verdict is set.

## Current state

```json
{{state}}
```

{{instruction}}

## Output contract

```json
{
  "blocked": "reason, or null",
  "failure_class": "A|B|C",
  "class_evidence": "why this class, citing run ids",
  "verdict": "refutes|inconclusive|null",
  "findings": "markdown for the node's Findings section, citing run ids",
  "conclusions": "markdown for the node's Conclusions section, including the failure class, the run ids, and what would change the answer",
  "investigation_test": {
    "named_cause":    {"pass": true, "note": "the specific mechanism"},
    "discriminating": {"pass": true, "note": "the two explanations and what each predicts"},
    "novel":          {"pass": true, "note": "what in the tree was checked"},
    "in_scope":       {"pass": true, "note": ""},
    "proportionate":  {"pass": true, "note": "expected information against cost"},
    "named_metric":   {"pass": true, "note": "the metric name and its direction"}
  },
  "proceed": true,
  "blocking_condition": "name of the failed condition, or null",
  "children": [
    {
      "id": "lowercase-slug",
      "kind": "diagnostic|ablation|hypothesis",
      "expectation": "one falsifiable sentence",
      "scope": "markdown: which result of the parent this responds to",
      "expectation_body": "markdown: prediction, then what would refute it"
    }
  ]
}
```
