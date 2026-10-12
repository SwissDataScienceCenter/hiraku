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

## Comparison tasks: which methods to build

Read the task's `## Proposed protocol` (a `**Candidate methods**` block) and its goal and
body to see whether it compares methods.

- **The task names the methods** (status `specified`, or named in the goal/body): build
  exactly those. Each named method is its own sibling `hypothesis` node, a child of the
  `baseline` (or a root if there is no baseline yet), with `origin` set to `researcher`
  because the researcher chose it. Write its `expectation` against the task's metrics and
  decision rule. Do not add methods the task did not name unless the instruction below asks
  for more; if you think one is missing, say so in `notes`.
- **The task compares methods without naming them** (status `unspecified`), and the
  instruction below names none either: do NOT invent a list from memory. Propose only the
  `baseline` if it is missing, and say in `notes` that the candidates have to come from
  `hiraku-agent discover` (which records where each one came from) before they can be
  built.
- If the instruction below names methods, those are the ones to build, whatever the task
  says.

## Values the plan depends on that the task does not give

A node needs values the task deliberately does not state: file paths, thresholds,
preprocessing, hyperparameters, checkpoints and model variants, hardware. The task keeps
only what makes the experiments comparable, so these live here, on the node. Settle each
one by the rules on unknown values below (look, default, choose, gap) and list it on the
node under `open_items` with its status: `found`, `default`, `choice` or `unset`. The
researcher reviews this list and corrects what they disagree with with one `update`; you do
not stop to ask about it.

If you have no way to read the project (no context file, no repository), say so in
`notes` once, and use `default` or `unset` rather than guessing a path or a number a
repository would settle.

Values that are shared by every sibling (a ground-truth file, a production threshold, a
fixed configuration of a component all nodes use) belong in one place: state them once in
the baseline node's `open_items` and refer to them from the others instead of repeating
or varying them.

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
      "rationale": "why this node should exist, and why here in the tree",
      "open_items": [
        {
          "item": "what the plan depends on",
          "value": "the value, or null if it cannot be determined",
          "status": "found|default|choice|unset",
          "source": "file it was read from, the reason for the default, or the options and why you picked one"
        }
      ]
    }
  ],
  "notes": "only what could not be read, or an irreversible or expensive decision; not values you could look up, default or choose, or null"
}
```
