# Skill reference: create an experiment in the tree

Loaded when creating a root experiment under a goal, or a child of
an existing node.

## Decide the position first

- **Root** — a new independent attempt at the goal. No `parent`. `goal` required.
- **Child** — a response to a specific node's result. `parent` set; `goal` inherited.
- **Combination** — tests whether two nodes' effects compose. `kind: combination`, `parents` as a
  list, no `parent`.

If you cannot say which node's result this responds to, it is a root, not a child. Attaching an
unrelated idea to whatever node was most recently touched is how the tree stops meaning anything.

## Choose `kind`

| `kind` | Use when |
|---|---|
| `baseline` | Establishes the reference the goal is measured against. Usually the first root. |
| `hypothesis` | Predicts a specific improvement by a stated mechanism. |
| `diagnostic` | Explains a parent's result. Often adds instrumentation rather than changing the method. |
| `ablation` | Isolates the contribution of one component of a node that already worked. |
| `combination` | Composes two or more nodes that each worked. |

## Write the expectation before anything runs

`expectation` in front matter — one falsifiable sentence — plus the body section stating what result
would refute it. This is not paperwork: it is the only thing that makes the eventual `verdict`
meaningful, and [triage-failure.md](triage-failure.md) refuses to triage a node that lacks it.

A node whose expectation is "see whether X helps" is not ready. What is the predicted direction, on
which metric, of roughly what size, and by what mechanism?

## Create

1. Validate the ID against `^[a-z0-9]+(?:-[a-z0-9]+)*$`, unique across all experiments, and never
   previously used. Name the question, not the tree position.
2. Confirm `goal` exists and is `active`, and that `parent` (or every entry of `parents`) exists.
3. `exp create <id>` with the existing flow, then add `goal`, `parent`, `kind`, `status`, `origin`,
   `expectation`, `created` to the front matter.
4. Branch is `exp/<id>` — **flat**. Never `exp/<parent>/<child>`: git cannot hold both
   `refs/heads/exp/foo` and `refs/heads/exp/foo/bar`, so a hierarchical name breaks as soon as the
   parent has its own branch.
5. Copy `policy.md` from the parent when there is one, narrowing if appropriate. Never widen it, and
   never give a child of a guided node `mode: auto`.
6. Inherit the goal's evaluation protocol verbatim. A node needing a different protocol belongs under
   a different goal — escalate rather than editing it.
7. For a long-running node expected to run alongside its siblings, add a worktree:
   `git worktree add .worktrees/<id> exp/<id>`.
8. Append the new node to the parent's **Children** section.

## Status on creation

`proposed` when it awaits a decision; `approved` once authorised but not yet started; `running`
after the first launch. In a guided node, creating a child at all is a checkpoint — ask first.

## Never

- Create a child of an open node. Conclude the parent first.
- Reuse a retired ID.
- Write `hypothesis_id` into the run table. `experiment_id` already identifies the node.
- Copy the parent's metrics into the child's record. Runs are read from the table.
