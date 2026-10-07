{{rules}}

---

# Task: revise the experiments already designed under a task

`design` or `discover` wrote a set of experiments under one task, and the researcher wants
them revised in one pass. You see the task, every experiment that may still be changed
(`editable`), and the ones that may not (`not_editable`, with the reason: they already
have runs, so their expectation has results recorded against it). Return the changes as
JSON; you do not write files, the tool applies what you return.

Rules specific to this workflow:

- Change only what the instruction asks for, and only for the nodes it concerns. Return an
  entry for a node only if something about it changes; leave the others out. Never touch
  an `id`, the parent, the commit or the entrypoints, and never return a node that is not
  under `editable`.
- Look at all the nodes together before you write any of them. A value several siblings
  depend on (a ground-truth file, a threshold, a fixed configuration of a component they
  all use) must be the same in every node: settle it once, state it in the baseline node's
  `## Open items`, and refer to it from the others instead of repeating or varying it.
- Where the instruction leaves a detail open, resolve it the way `design` does. Read a
  value from the project context or a repository you can read and name the file; otherwise
  give a conventional default marked as an initial value to calibrate; for a real choice
  between options pick the one you recommend and label it. Only an irreversible or
  expensive decision goes in `notes`. A proposal is fine, an invented measurement is not.
- `body` is the **complete new Markdown body** of that node, not a patch: copy its current
  body and edit it. Keep the `## Open items` section in the same shape (`- **item** —
  value (found in the project; file | default, to be calibrated; reason | recommended
  choice, change if you disagree; options | unset, needs the researcher)`). Return `null` if the body of that node does not change.
- `expectation` is one falsifiable sentence, with a predicted direction, a metric named in
  the task's `metrics`, a rough size and a mechanism. `kind` is one of `baseline`,
  `hypothesis`, `diagnostic`, `ablation`, `combination`. Return `null` to leave either
  unchanged. Changing an expectation clears its approval, so do not change one that the
  instruction does not concern.
- A node's evaluation protocol belongs to the task. If the instruction needs a different
  metric or protocol, change nothing for it and say in `notes` that the task needs a
  revision.
- `notes`: one or two sentences, what you changed and why, plus anything the researcher
  must still decide. `null` if there is nothing to add.

## Current state

```json
{{state}}
```

{{instruction}}

## Output contract

Return ONE JSON object and nothing else.

```json
{
  "nodes": {
    "<editable-node-id>": {
      "expectation": "new expectation, or null",
      "kind": "new kind, or null",
      "body": "the complete new markdown body, or null"
    }
  },
  "notes": "what changed and anything left to decide, or null"
}
```
