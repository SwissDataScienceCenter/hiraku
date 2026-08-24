# Bounded auto experiment policy

Approval of this policy must be obtained in the active conversation before each loop. Its presence
in the repository is not approval.

## Approved scope

Pose-consistency verification on retrieved pairs, and diagnostics of its own failure modes. The
descriptor, the retrieval stage, and the goal's evaluation protocol are fixed and out of scope.

## Evaluation protocol

The `same-place-verification` protocol, unmodified. Splits are never regenerated.

## Permitted commands

`uv run python -m pr.verify_pose`, `uv run python -m pr.diagnose`, and read-only git. Nothing that
installs dependencies, writes outside the project, or reaches the network.

## Resource limits

12 runs remaining under this node and its children; depth 4 from the goal root; 6 wall-clock hours
per run; at most 2 concurrent worktrees.

## Stopping conditions

Success: precision at or above 0.95 with recall above 0.858, reproduced at a second seed.
Exhaustion: no child passes the investigation test. Failure: the same class of failure three times.
Escalation: any bound above, any protocol or scope change, any new dependency or dataset.

Within these bounds the agent may edit code, commit, launch named entrypoints, read results, and
choose the next in-scope iteration. It must pause before changing scope, evaluation, permissions,
limits, stopping conditions, or mode, and must never merge a protected branch without explicit human
approval. A child of this node may narrow this policy; it may never widen it.

This policy guides agent behavior. It is not a security boundary; repository protections,
sandboxing, credentials, and execution infrastructure remain authoritative.
