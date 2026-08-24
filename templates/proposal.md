---
id: <proposal-id>
goal: <goal-id>
origin: agent-literature    # agent-literature | agent-diagnostic
proposed_parent:            # omit if this would be a root
kind: hypothesis            # hypothesis | diagnostic | ablation | combination
status: proposed            # proposed | accepted | declined | deferred
proposed_on: <YYYY-MM-DD>
decided_on:
---

# <Proposal title>

## Hypothesis

One falsifiable sentence, in this project's terms rather than the source's.

## Where it came from

For a literature proposal, each source: what it **claims**, under **what conditions**, and the
**strength of that evidence**. A widely reproduced result and a single-dataset preprint are different
inputs, and which one this is matters more than the claim.

Cite the source. Never restate its claim as a project finding — findings come from this project's
runs.

For a diagnostic proposal, the run IDs and the observation that motivated it.

## Why it applies here

The concrete bridge from the source's setting to this project's data, scale, and protocol. Most
literature-derived ideas should die here; a proposal that cannot make this argument should be
declined rather than deferred.

## What it predicts

The expected effect on the goal's `primary_metric`, roughly how large, and the result that would
refute it.

## Investigation test

1. **Named cause** — the specific suspected mechanism.
2. **Discriminating** — the two explanations this separates, and what each predicts.
3. **Novel** — nothing in the goal's tree, including `abandoned` nodes and declined proposals,
   already tested this. Say what was checked.
4. **In scope** — inside the goal's scope, under its unmodified protocol.
5. **Within budget** — runs and depth remaining.
6. **Proportionate** — expected information against the cost.

## Cost

Runs, wall-clock, and whether new data, dependencies, or credentials are needed. Any of those three
escalates to the researcher regardless of mode.

## What it would take to decline

The strongest argument against doing this. Required: an agent that cannot argue the other side has
retrieved an idea rather than evaluated it.

## Decision

Filled in by the researcher: accepted, declined, or deferred, with reasoning and the date. Declined
proposals are never deleted — they are what stops the idea being proposed again.
