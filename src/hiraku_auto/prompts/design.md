{{rules}}

---

# Task: design experiments for a goal

You are given a goal and the current tree beneath it. Propose the experiments that should exist to
answer the goal's question, as a tree — including which node each is a child of, and why.

Rules specific to this task:

- If the goal has no `baseline` node, one is almost always the first thing needed: nothing else can
  be measured without a reference.
- Independent attempts at the goal are siblings. A node is a CHILD only if it responds to a specific
  result of its parent — if you cannot say which result, it is a sibling or a root.
- Every node needs an `expectation`: one falsifiable sentence with a predicted direction, a metric,
  a rough size, and a mechanism. "See whether X helps" is not an expectation and will be rejected.
- Propose few, well-argued nodes rather than many. A tree nobody runs is worse than no tree.
- Do not propose nodes that duplicate anything already in the tree.
- Do not invent metric values or claim what results will be.

## Current state

```json
{{state}}
```

{{instruction}}

## Output contract

```json
{
  "experiments": [
    {
      "id": "lowercase-slug",
      "kind": "baseline|hypothesis|diagnostic|ablation|combination",
      "parent": "existing-node-id or null",
      "expectation": "one falsifiable sentence",
      "scope": "markdown: what this tests and, if a child, which result of the parent it responds to",
      "expectation_body": "markdown: predicted direction, size, mechanism; then what would refute it",
      "rationale": "why this node should exist, and why here in the tree"
    }
  ],
  "notes": "anything the researcher should decide before these are created, or null"
}
```
