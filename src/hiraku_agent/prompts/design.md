{{rules}}

---

# Task: design experiments for a task

You are given a task and the current tree of nodes beneath it. Propose the experiments
that should exist to answer the task's question, as a tree — including which node each is
a child of, and why.

Rules specific to this workflow:

- If the task has no `baseline` node, one is almost always the first thing needed: nothing
  else can be measured without a reference.
- Independent attempts at the task are siblings. A node is a CHILD only if it responds to
  a specific result of its parent — if you cannot say which result, it is a sibling or a
  root.
- Every node needs an `expectation`: one falsifiable sentence with a predicted direction,
  a metric named in the task's `metrics` list, a rough size, and a mechanism. "See whether
  X helps" is not an expectation and will be rejected.
- Propose few, well-argued nodes rather than many. A tree nobody runs is worse than no
  tree.
- Do not propose nodes that duplicate anything already in the tree.
- Do not invent metric values or claim what results will be.

## When the researcher supplies the idea

If the instruction below names a specific idea, direction, or hypothesis, your job is to
turn THAT into one or more well-formed nodes — not to also run your own separate
tree-completion agenda unless asked. Work it up in the researcher's own terms: the right
`kind`, the correct `parent` (or say plainly that it is a new root), a falsifiable
`expectation`, and a `scope` that states exactly what it tests. Set `origin` to
`researcher`. If the idea as stated already exists in the tree, or contradicts something
already settled there, say so in `notes` instead of filing a duplicate node.

If no instruction is given, propose what the tree needs next on its own terms, as before
— the rules above still apply. Set `origin` to `agent-diagnostic` for these.

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
      "origin": "researcher if the instruction supplied the idea, agent-diagnostic if you proposed it",
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
