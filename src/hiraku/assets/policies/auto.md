# Bounded auto experiment policy

Complete every section below with concrete limits. Before starting the autonomous loop, obtain
explicit approval of this policy in the active conversation. Approval is not persisted in this
file and must not be inferred from its presence.

## Approved scope

Define the implementation and research questions that may be explored.

## Evaluation protocol

Define immutable datasets, splits, metrics, and comparison criteria.

## Permitted commands

List exact commands or narrowly scoped command families the agent may execute.

## Resource limits

Define time, run-count, compute, storage, and cost limits.

## Stopping conditions

Define success, exhaustion, failure, and escalation conditions.

Within approved bounds, the agent may update code and documentation, commit, launch named
entrypoints, inspect results, and choose the next in-scope iteration. It must pause before changing
scope, evaluation, permissions, resource limits, stopping conditions, or mode. It must never merge
into a protected branch without explicit human approval.

This policy guides agent behavior. It is not a security boundary; repository protections,
sandboxing, credentials, and execution infrastructure remain authoritative.

