{{rules}}

---

# Task: triage a concluded node and decide whether to go further

You are given one node, its runs, its task, and the surrounding tree. This node's `state`
is `concluded` or `awaiting_analysis` — either it already has a verdict with no children,
or it has runs and none yet; judge from what's in `focus_node` below rather than assuming
which. A `supports` verdict lands here too: a positive result is not automatically the end
of this branch, any more than a negative one is — the question is the same either way
("is there a good reason to go deeper"), just answered through a different lens depending
on which kind of verdict you're looking at.

Do this in order:

1. **Check preconditions.** If the node has no recorded `expectation`, stop: set
   `"blocked": "no expectation recorded"` and return. If every run is
   `reproducible: false`, say so — class A cannot be excluded.
2. **Judge the result**, depending on what it was:
   - **`refutes` or `inconclusive` (or no verdict yet)** — classify the miss into exactly
     one of failure classes A, B, C, with the evidence from the run rows (see
     `prompts/_rules.md`). Set `failure_class` and `class_evidence`.
   - **`supports`** — there is no failure to classify. Set `failure_class` and
     `class_evidence` to `null`, and instead name the specific further question (if any)
     this result raises that the task's own evaluation protocol hasn't already answered —
     does it hold under a different seed or split, does a natural ablation isolate which
     part of the change actually mattered, does pushing the same lever further still help.
     "It worked, let's see if it keeps working" is not itself a reason; name the concrete
     mechanism in question, the same way the investigation test below requires.
3. **Conclude the node** — write `findings` and `conclusions` for THIS node, citing run
   IDs. A child must never be created while its parent is still open.
4. **Apply the investigation test** for any child you would propose. All six conditions,
   answered, regardless of which branch of step 2 you took — a follow-up to a `supports`
   result needs a named cause and a discriminating test exactly as much as a diagnostic
   does; "confirm it again" or "try a bigger version" fails `named_cause` the same way it
   would for a miss.
5. **Decide.** If any condition fails, `proceed` is false and `blocking_condition` names
   it.

For class A, `proceed` is false and `children` is empty: a defect is fixed on the same
node, and no verdict is set. For a `supports` verdict with nothing further worth asking,
`proceed` is false, `children` is empty, and `blocking_condition` names whichever
investigation-test condition the candidate follow-up failed (usually `named_cause` or
`discriminating`) — concluding "no further question earns its cost" is itself the decision
this call exists to make, not a failure to make one.

## Current state

```json
{{state}}
```

{{instruction}}

## Output contract

```json
{
  "blocked": "reason, or null",
  "failure_class": "A|B|C|null (null when the verdict is supports — nothing to classify)",
  "class_evidence": "why this class, citing run ids — null when failure_class is null",
  "verdict": "refutes|inconclusive|null",
  "findings": "markdown for the node's Findings section, citing run ids",
  "conclusions": "markdown for the node's Conclusions section — the failure class and run ids for a miss, or why this supports result does or doesn't warrant a follow-up",
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
